/* ============================================================================
   SOULY — classroom device.  FINAL FIRMWARE.

   A teacher taps in, a lesson clock runs, the lamp blinks and the screen names
   a child when the camera flags them, and the teacher taps out.

   `souly_selftest/` is the diagnostic sketch and stays untouched. Flash that
   when hardware misbehaves. Flash THIS one to run the device.

   ---------------------------------------------------------------------------
   THE ARCHITECTURE, AND WHY IT IS NOT A SINGLE LOOP
   ---------------------------------------------------------------------------
   There are two tasks on two cores, and that is the single most important
   thing in this file.

       core 1 (Arduino loop)   LCD, LED, card reader.  Never blocks.
       core 0 (netTask)        WiFi and every HTTP call.  May block freely.

   This is not premature engineering. It is a direct response to a documented
   bug: on ESP32, `WiFiClient::connect(ip, port, timeout)` and
   `HTTPClient::setConnectTimeout()` are NOT reliably honoured
   (arduino-esp32 issues #5168 and #7057). A call that should abort after
   800ms can block for thirty seconds instead.

   That is exactly the failure that cost two days: the screen froze on
   "Connected" and looked crashed, while the sketch sat inside an HTTP call
   whose timeout was being ignored. No amount of setting timeouts fixes it,
   because setting timeouts is the thing that doesn't work.

   Moving the network off the rendering thread makes the bug harmless. If an
   HTTP call hangs, the clock still ticks, the lamp still blinks and the reader
   still reads. The device degrades instead of appearing dead.

   The two tasks share NO peripherals — SPI and I2C are touched only from
   core 1, WiFi only from core 0 — so there is nothing to race over except one
   small mailbox, which a mutex covers.

   ---------------------------------------------------------------------------
   THE SCREEN IS RENDERED ON THE SERVER
   ---------------------------------------------------------------------------
   Every endpoint returns four strings already padded to 20 columns, plus what
   the lamp should do. This sketch prints them; it does not decide what they
   say. Everything that can be WRONG — session timing, which flag to show, how
   to abbreviate a name, when to suppress a repeat — lives in Python, in
   app/routers/device.py, where it is testable and changeable without a
   reflash.

   ---------------------------------------------------------------------------
   THE FOUR OTHER BUGS THIS FILE IS WRITTEN AROUND
   ---------------------------------------------------------------------------
   1. TX POWER.  Cheap ESP32-S3 N16R8 boards have poor RF matching; at the
      default 19.5 dBm the WPA2 handshake frames arrive corrupted and the AP
      gives up — "reason 2, AUTH_EXPIRE". connectWiFi() walks a power ladder
      down until one associates. This is why the device would not join ANY
      network, including a phone hotspot.

   2. THE READER DIES.  An MFRC522 stops answering after a few hours
      (miguelbalboa/rfid #540, #546). rfidWatchdog() reads its version register
      every 5 seconds and re-initialises it if it has gone quiet — so it heals
      itself instead of silently ignoring cards for the rest of a lesson.

   3. I2C CAN LOCK UP.  Wire can block forever on a glitching device
      (arduino-esp32 #349). Wire.setTimeOut() bounds it.

   4. HEAP FRAGMENTATION.  Arduino `String` churn kills long-running ESP32
      sketches. The network path uses fixed char buffers, not String, and free
      heap is logged every 30s so a leak is visible rather than fatal.

   Plus: every millis() comparison is written as (now - then >= interval), the
   unsigned form that survives the 49-day rollover.

   ---------------------------------------------------------------------------
   WIRING  (verified)
   ---------------------------------------------------------------------------
     RC522  SDA/SS -> 10   SCK -> 12   MOSI -> 11   MISO -> 13   RST -> 9
            VCC    -> 3.3V  ** never 5V **
     LCD    SDA    -> 4    SCL -> 5    VCC -> 5V
     LED    GPIO 1 + 220-330R -> GND

   R8 part = 8MB octal PSRAM, so GPIO 33-37 are unusable. LCD is a 2004A:
   20 columns, not 16.

   Arduino IDE: ESP32S3 Dev Module, USB CDC On Boot = ENABLED.
   Libraries: LiquidCrystal_I2C, MFRC522 1.4.x (not v2), ArduinoJson 7.x.
   ============================================================================ */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

#include "secrets.h"


/* ---------------------------------------------------------------------------
   Pins and geometry
   --------------------------------------------------------------------------- */
#define PIN_LED   1
#define PIN_BOOT  0          // stands in for a card when USE_RFID is 0
#define PIN_SDA   4
#define PIN_SCL   5

#define RC_SS     10
#define RC_RST    9
#define RC_SCK    12
#define RC_MOSI   11
#define RC_MISO   13

#define LCD_COLS  20
#define LCD_ROWS  4

// 1 = the RC522.  0 = the BOOT button sends BOOT_CARD_UID instead, so the
// whole flow can be demonstrated with no reader at all. The card appears in
// exactly one function, readCard(); nothing else knows or cares.
#define USE_RFID  0
#define BOOT_CARD_UID "62EA2F03"     // Sarah Ahmed, who teaches class 1


/* ---------------------------------------------------------------------------
   Timings.  Every one of these is a deliberate number.
   --------------------------------------------------------------------------- */
static const uint32_t POLL_SESSION_MS  = 1000;   // during a lesson
static const uint32_t POLL_IDLE_MS     = 3000;   // nothing is happening
static const uint32_t CARD_SCAN_MS     = 50;
static const uint32_t CARD_REPEAT_MS   = 3000;   // ignore the same card this long
static const uint32_t RFID_WATCHDOG_MS = 5000;
static const uint32_t HEAP_LOG_MS      = 30000;
static const uint32_t WIFI_RETRY_MS    = 5000;
static const uint32_t HTTP_CONNECT_MS  = 1500;
static const uint32_t HTTP_READ_MS     = 1500;
static const uint32_t TAP_FAIL_HOLD_MS = 2000;
static const int      MISSES_BEFORE_COMPLAINING = 10;

#define RESP_MAX 1400        // device.py responses run ~400 bytes


/* ===========================================================================
   THE MAILBOX — the only thing the two cores share
   ---------------------------------------------------------------------------
   netTask writes it, the render loop reads it. Plain fixed-size fields, no
   String, no pointers, so copying it under the mutex is trivial and cannot
   allocate.
   =========================================================================== */
struct Mailbox {
  char     lines[LCD_ROWS][LCD_COLS + 1];
  bool     linesFresh;

  int      ledCount;
  bool     ledPulse;
  bool     ledFresh;

  bool     backlight;
  bool     backlightFresh;

  uint32_t holdMs;          // consumed with the lines
  bool     inSession;
  bool     wifiUp;
  int      misses;          // consecutive failed polls
  char     txPower[24];
};

static Mailbox        box;
static SemaphoreHandle_t boxLock;
static QueueHandle_t  tapQueue;      // render loop -> netTask, card UIDs

struct TapMsg { char uid[24]; };


/* ---------------------------------------------------------------------------
   Peripherals.  Touched ONLY from core 1.
   --------------------------------------------------------------------------- */
LiquidCrystal_I2C *lcd = nullptr;
MFRC522 rfid(RC_SS, RC_RST);
static uint8_t lcdAddr = 0x27;

static char shownRow[LCD_ROWS][LCD_COLS + 1];   // what is physically on screen


/* ===========================================================================
   SCREEN  (core 1 only)
   =========================================================================== */

/* An HD44780 does not clear what was there before, so every line must be
   padded to the full width or the tail of the previous one survives. The
   server already pads, but a locally-drawn line still needs it. */
static void padInto(char *dst, const char *src) {
  int i = 0;
  for (; src && src[i] && i < LCD_COLS; i++) dst[i] = src[i];
  for (; i < LCD_COLS; i++) dst[i] = ' ';
  dst[LCD_COLS] = '\0';
}

/* A full 20x4 repaint over I2C is roughly 100ms. Repainting every poll would
   flicker visibly for an entire lesson, so only changed rows are written —
   during a lesson that is usually just the clock. */
static void drawRow(int row, const char *text) {
  if (!lcd || row < 0 || row >= LCD_ROWS) return;
  char line[LCD_COLS + 1];
  padInto(line, text);
  if (strcmp(line, shownRow[row]) == 0) return;
  lcd->setCursor(0, row);
  lcd->print(line);
  strcpy(shownRow[row], line);
}

static void drawScreen(const char *a, const char *b,
                       const char *c, const char *d) {
  drawRow(0, a); drawRow(1, b); drawRow(2, c); drawRow(3, d);
}


/* ===========================================================================
   LED  (core 1 only)
   ---------------------------------------------------------------------------
   A state machine, never delay(). Three blinks at 200ms would be 1.2 seconds
   with the reader deaf and the screen frozen — a self-inflicted freeze
   indistinguishable from a crash, which is the thing this whole firmware is
   organised to avoid.
   =========================================================================== */
static struct {
  int           remaining = 0;
  bool          on        = false;
  unsigned long last      = 0;
  uint32_t      gap       = 0;
  bool          pulse     = false;
} ledState;

static void ledBlink(int times, bool pulse) {
  if (times <= 0) return;
  ledState.remaining = times * 2;
  ledState.on        = false;
  ledState.pulse     = pulse;
  ledState.gap       = 0;
  ledState.last      = millis() - 1000;    // fire on the next tick
}

static void ledService() {
  if (ledState.remaining <= 0) return;
  unsigned long now = millis();
  if (now - ledState.last < ledState.gap) return;

  ledState.on = !ledState.on;
  digitalWrite(PIN_LED, ledState.on ? HIGH : LOW);
  ledState.remaining--;
  ledState.last = now;
  // A pulse is slow and even; a blink is quick. The teacher reads the rhythm
  // before they read the screen, so "the room is drifting" and "this one
  // child" must not feel the same.
  ledState.gap = ledState.pulse ? 600 : (ledState.on ? 180 : 220);

  if (ledState.remaining <= 0) digitalWrite(PIN_LED, LOW);
}


/* ===========================================================================
   CARD  (core 1 only)
   =========================================================================== */
static void uidToHex(MFRC522::Uid &uid, char *out, size_t n) {
  size_t p = 0;
  for (byte i = 0; i < uid.size && p + 2 < n; i++)
    p += snprintf(out + p, n - p, "%02X", uid.uidByte[i]);
  out[p] = '\0';
}

static char          lastUid[24] = "";
static unsigned long lastUidMs   = 0;

/* An MFRC522 stops answering after a few hours (miguelbalboa/rfid #540).
   The symptom is silent: PICC_IsNewCardPresent() just returns false forever
   and the device ignores every card for the rest of the day. Reading the
   version register costs one SPI transaction, so check it and heal. */
static void rfidWatchdog() {
#if USE_RFID
  static unsigned long last = 0;
  unsigned long now = millis();
  if (now - last < RFID_WATCHDOG_MS) return;
  last = now;

  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  if (v == 0x91 || v == 0x92) return;

  Serial.printf("RFID went quiet (0x%02X) — reinitialising\n", v);
  digitalWrite(RC_RST, LOW);
  delay(2);
  digitalWrite(RC_RST, HIGH);
  delay(50);
  rfid.PCD_Init();
  rfid.PCD_AntennaOn();
#endif
}

/* The only place a card is read. Returns true and fills `out` on a fresh tap.

   The RC522 reports the same card continuously while it sits in the field, so
   without the repeat guard one physical tap becomes start / end / start about
   twenty times a second — and the demo dies in front of a judge. */
static bool readCard(char *out, size_t n) {
  unsigned long now = millis();
  char uid[24] = "";

#if USE_RFID
  if (!rfid.PICC_IsNewCardPresent()) return false;
  if (!rfid.PICC_ReadCardSerial())   return false;
  uidToHex(rfid.uid, uid, sizeof(uid));
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
#else
  if (digitalRead(PIN_BOOT) != LOW) return false;
  strncpy(uid, BOOT_CARD_UID, sizeof(uid) - 1);
#endif

  if (strcmp(uid, lastUid) == 0 && now - lastUidMs < CARD_REPEAT_MS) return false;

  strncpy(lastUid, uid, sizeof(lastUid) - 1);
  lastUidMs = now;
  strncpy(out, uid, n - 1);
  out[n - 1] = '\0';
  return true;
}


/* ===========================================================================
   WIFI  (core 0 only)
   =========================================================================== */
static int lastReason = 0;

static const char *reasonText(int r) {
  switch (r) {
    case 2:   return "AUTH EXPIRED (on this board: TX power)";
    case 15:  return "handshake timeout -> wrong password";
    case 201: return "no AP found -> wrong SSID, or 5GHz only";
    case 202: return "auth failed";
    case 203: return "association refused";
    case 204: return "handshake timeout";
    default:  return "see esp_wifi_types.h";
  }
}

static void onWiFiEvent(WiFiEvent_t e, WiFiEventInfo_t info) {
  if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    lastReason = info.wifi_sta_disconnected.reason;
}

/* THE TX POWER LADDER — see the header. This board cannot complete a WPA2
   handshake at full power, so walk down until one sticks. Kept in the final
   firmware, not hardcoded to whatever worked once, so the device still comes
   up on a different network at the venue. setTxPower() must come AFTER
   begin(). */
struct PowerStep { wifi_power_t value; const char *label; };
static PowerStep powerLadder[] = {
  {WIFI_POWER_19_5dBm, "19.5dBm"},
  {WIFI_POWER_11dBm,   "11dBm"},
  {WIFI_POWER_8_5dBm,  "8.5dBm"},
  {WIFI_POWER_5dBm,    "5dBm"},
};
static const int POWER_STEPS = sizeof(powerLadder) / sizeof(powerLadder[0]);
static int goodPower = -1;      // remembered once found, tried first next time

static bool connectAt(int step, uint32_t budgetMs) {
  WiFi.persistent(false);
  WiFi.mode(WIFI_OFF);          // tear down anything still half-alive: without
  delay(200);                   // this, begin() is refused with
  WiFi.mode(WIFI_STA);          // "sta is connecting, cannot set config"
  WiFi.setSleep(false);         // power saving mid-handshake loses packets
  WiFi.setAutoReconnect(true);
  WiFi.disconnect(true, true);
  delay(200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  WiFi.setTxPower(powerLadder[step].value);

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < budgetMs)
    vTaskDelay(pdMS_TO_TICKS(200));

  return WiFi.status() == WL_CONNECTED;
}


static bool connectWiFi() {
  // Try the power that worked last time first — a reconnect mid-lesson should
  // take seconds, not half a minute walking the whole ladder again.
  if (goodPower >= 0 && connectAt(goodPower, 9000)) return true;

  for (int i = 0; i < POWER_STEPS; i++) {
    if (i == goodPower) continue;
    Serial.printf("wifi: trying %s\n", powerLadder[i].label);
    if (connectAt(i, 9000)) {
      goodPower = i;
      Serial.printf("wifi: up at %s  ip %s  rssi %d\n",
                    powerLadder[i].label,
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
    Serial.printf("wifi: %s failed (reason %d - %s)\n",
                  powerLadder[i].label, lastReason, reasonText(lastReason));
  }
  return false;
}


/* ===========================================================================
   HTTP  (core 0 only)
   ---------------------------------------------------------------------------
   Fixed char buffers throughout: no String in the per-second path, because
   String churn fragments the ESP32 heap and eventually crashes a sketch that
   has been up for hours.

   The timeouts below are set honestly but NOT relied upon — see the header.
   Being on a separate core is what actually protects the device.
   =========================================================================== */
static char respBuf[RESP_MAX];

static int httpCall(const char *method, const char *path,
                    const char *body, bool wantBody) {
  if (WiFi.status() != WL_CONNECTED) return -100;

  char url[128];
  snprintf(url, sizeof(url), "http://%s:%d%s", SERVER_HOST, SERVER_PORT, path);

  WiFiClient net;
  HTTPClient http;

  if (!http.begin(net, url)) return -101;

  http.setConnectTimeout(HTTP_CONNECT_MS);
  http.setTimeout(HTTP_READ_MS);
  // Deliberately OFF. A kept-alive socket the server has since closed stalls
  // inside begin(), before any timeout could apply.
  http.setReuse(false);
  http.addHeader("X-Souly-Device", DEVICE_KEY);

  int code;
  if (strcmp(method, "POST") == 0) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST((uint8_t *)body, body ? strlen(body) : 0);
  } else {
    code = http.GET();
  }

  respBuf[0] = '\0';
  if (code == 200 && wantBody) {
    int len = http.getSize();
    WiFiClient *s = http.getStreamPtr();
    size_t p = 0;
    unsigned long t0 = millis();
    while (p < sizeof(respBuf) - 1 && millis() - t0 < HTTP_READ_MS) {
      if (!s->connected() && !s->available()) break;
      int avail = s->available();
      if (avail <= 0) { vTaskDelay(pdMS_TO_TICKS(5)); continue; }
      int got = s->readBytes(respBuf + p, min((size_t)avail, sizeof(respBuf) - 1 - p));
      if (got <= 0) break;
      p += got;
      if (len > 0 && (int)p >= len) break;
    }
    respBuf[p] = '\0';
  }

  http.end();
  return code;
}


/* Parses one server response into the mailbox. Runs on core 0, so ArduinoJson
   never allocates on the rendering thread. */
static void ingest(const char *json) {
  if (!json || !json[0]) return;

  JsonDocument doc;
  if (deserializeJson(doc, json)) {
    Serial.println(F("bad JSON from server"));
    return;
  }

  JsonArray lines = doc["lines"];
  const char *state = doc["state"] | "";
  uint32_t hold     = doc["hold_ms"] | 0;
  const char *pat   = doc["led"]["pattern"] | "none";
  int cnt           = doc["led"]["count"]   | 0;
  bool bl           = doc["backlight"] | true;

  if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(200)) == pdTRUE) {
    if (!lines.isNull()) {
      for (int i = 0; i < LCD_ROWS; i++) {
        const char *s = (i < (int)lines.size()) ? lines[i].as<const char *>() : "";
        padInto(box.lines[i], s ? s : "");
      }
      box.linesFresh = true;
      box.holdMs     = hold;
    }
    box.inSession = (strcmp(state, "session") == 0 ||
                     strcmp(state, "flag") == 0 ||
                     strcmp(state, "flag_room") == 0);
    if (cnt > 0 && strcmp(pat, "none") != 0) {
      box.ledCount = cnt;
      box.ledPulse = (strcmp(pat, "pulse") == 0);
      box.ledFresh = true;
    }
    box.backlight      = bl;
    box.backlightFresh = true;
    xSemaphoreGive(boxLock);
  }

  // Tell the server what actually reached the teacher's eyes. Doing this here
  // rather than inside /poll means a flag lost to a dropped packet is shown on
  // the next poll instead of being silently swallowed — and it makes "was the
  // teacher told?" a fact rather than an assumption.
  JsonArray ids = doc["flag_ids"];
  if (!ids.isNull() && ids.size() > 0) {
    char payload[160];
    size_t p = snprintf(payload, sizeof(payload), "{\"flag_ids\":[");
    for (size_t i = 0; i < ids.size() && p < sizeof(payload) - 16; i++)
      p += snprintf(payload + p, sizeof(payload) - p, "%s%d",
                    i ? "," : "", ids[i].as<int>());
    snprintf(payload + p, sizeof(payload) - p, "]}");
    httpCall("POST", "/api/device/shown", payload, false);
  }
}

static void setLocal(const char *a, const char *b, const char *c, const char *d,
                     uint32_t hold, int blinks) {
  if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(200)) != pdTRUE) return;
  padInto(box.lines[0], a); padInto(box.lines[1], b);
  padInto(box.lines[2], c); padInto(box.lines[3], d);
  box.linesFresh = true;
  box.holdMs     = hold;
  box.backlight = true; box.backlightFresh = true;
  if (blinks) { box.ledCount = blinks; box.ledPulse = false; box.ledFresh = true; }
  xSemaphoreGive(boxLock);
}


/* ===========================================================================
   netTask — core 0.  Everything here is allowed to block.
   =========================================================================== */
static void netTask(void *) {
  WiFi.onEvent(onWiFiEvent);

  unsigned long lastPoll  = 0;
  unsigned long lastRetry = 0;
  unsigned long lastHeap  = 0;
  bool          greeted   = false;
  int           helloFails = 0;

  for (;;) {
    unsigned long now = millis();

    /* --- keep the link up ------------------------------------------------ */
    if (WiFi.status() != WL_CONNECTED) {
      if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(50)) == pdTRUE) {
        box.wifiUp = false; xSemaphoreGive(boxLock);
      }
      if (now - lastRetry >= WIFI_RETRY_MS) {
        lastRetry = millis();
        if (connectWiFi()) {
          greeted = false;                 // say hello again on a fresh link
          if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(50)) == pdTRUE) {
            box.wifiUp = true;
            strncpy(box.txPower, powerLadder[goodPower].label,
                    sizeof(box.txPower) - 1);
            xSemaphoreGive(boxLock);
          }
        } else {
          // NEVER leave the screen sitting on a message that has stopped being
          // true. Without this the device shows "Starting..." forever when the
          // network is down — which is precisely the "looks frozen" failure
          // this firmware exists to prevent, just moved somewhere else.
          char reason[24];
          snprintf(reason, sizeof(reason), "reason %d", lastReason);
          setLocal("No WiFi", WIFI_SSID, reason, "retrying every 5s", 0, 0);
        }
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    /* --- hello, once per connection -------------------------------------- */
    if (!greeted) {
      int code = httpCall("POST", "/api/device/hello",
                          "{\"firmware\":\"souly-1.0\"}", true);
      if (code == 200) {
        greeted = true;
        helloFails = 0;
        ingest(respBuf);
        Serial.println(F("hello OK"));
      } else if (code == 401) {
        // Not transient and not survivable: say so and keep saying it.
        Serial.println(F("401 — device key rejected"));
        setLocal("Device key rejected", "", "Re-run seed_classes",
                 "and reflash", 0, 3);
        vTaskDelay(pdMS_TO_TICKS(5000));
        continue;
      } else {
        // WiFi is up but the laptop is not answering. Same rule as above: say
        // so rather than sitting on a stale screen. Five tries first, because
        // uvicorn may simply still be starting.
        Serial.printf("hello failed (%d)\n", code);
        if (++helloFails >= 5) {
          char l2[24];
          snprintf(l2, sizeof(l2), "%s:%d", SERVER_HOST, SERVER_PORT);
          setLocal("No server", l2, "Start run.bat, or", "check the firewall", 0, 0);
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
        continue;
      }
    }

    /* --- a tap jumps the queue ------------------------------------------- */
    TapMsg tap;
    if (xQueueReceive(tapQueue, &tap, 0) == pdTRUE) {
      char body[64];
      snprintf(body, sizeof(body), "{\"card_uid\":\"%s\"}", tap.uid);
      Serial.printf("tap %s\n", tap.uid);

      int code = httpCall("POST", "/api/device/tap", body, true);
      if (code == 200) {
        ingest(respBuf);
      } else {
        // A TAP MUST ALWAYS ANSWER. This is not the same rule as a poll: a
        // teacher who taps and sees nothing taps harder, then decides the
        // device is broken. A poll that fails stays silent; a tap never does.
        Serial.printf("tap failed (%d)\n", code);
        setLocal("No server", "", "Try again", "", TAP_FAIL_HOLD_MS, 3);
      }
      lastPoll = millis();
      continue;
    }

    /* --- the poll --------------------------------------------------------- */
    bool inSession = false;
    if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(50)) == pdTRUE) {
      inSession = box.inSession; xSemaphoreGive(boxLock);
    }
    uint32_t every = inSession ? POLL_SESSION_MS : POLL_IDLE_MS;

    if (now - lastPoll >= every) {
      lastPoll = millis();
      int code = httpCall("GET", "/api/device/poll", nullptr, true);

      if (code == 200) {
        if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(50)) == pdTRUE) {
          box.misses = 0; xSemaphoreGive(boxLock);
        }
        ingest(respBuf);
      } else {
        // A POLL THAT FAILS CHANGES NOTHING ON SCREEN. A dropped packet on a
        // MiFi is normal and the next poll is a second away. Ten in a row is a
        // real outage and worth telling someone about.
        int m = 0;
        if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(50)) == pdTRUE) {
          m = ++box.misses; xSemaphoreGive(boxLock);
        }
        if (m == MISSES_BEFORE_COMPLAINING) {
          char l2[24];
          snprintf(l2, sizeof(l2), "%s:%d", SERVER_HOST, SERVER_PORT);
          setLocal("No reply from", l2, "Server off, or the",
                   "firewall is blocking", 0, 0);
        }
      }
    }

    /* --- heap watch ------------------------------------------------------- */
    if (now - lastHeap >= HEAP_LOG_MS) {
      lastHeap = millis();
      Serial.printf("heap %u free / %u min   rssi %d\n",
                    (unsigned)ESP.getFreeHeap(),
                    (unsigned)ESP.getMinFreeHeap(), WiFi.RSSI());
    }

    vTaskDelay(pdMS_TO_TICKS(20));
  }
}


/* ===========================================================================
   setup / loop — core 1.  Nothing here may block.
   =========================================================================== */
static unsigned long holdStart = 0;
static uint32_t      holdFor   = 0;

void setup() {
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  pinMode(PIN_BOOT, INPUT_PULLUP);
  pinMode(RC_RST, OUTPUT);
  digitalWrite(RC_RST, HIGH);

  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 1500) delay(10);

  Serial.println(F("\n\nSouly classroom device"));

  /* --- screen up FIRST, before anything that can be slow ----------------- */
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);
  Wire.setTimeOut(50);          // Wire can otherwise block forever on a
                                // glitching device (arduino-esp32 #349)
  for (uint8_t a : {0x27, 0x3F}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { lcdAddr = a; break; }
  }
  lcd = new LiquidCrystal_I2C(lcdAddr, LCD_COLS, LCD_ROWS);
  lcd->init();
  lcd->backlight();
  lcd->clear();
  for (int i = 0; i < LCD_ROWS; i++) memset(shownRow[i], 0, sizeof(shownRow[i]));

  // The device says something within a second of power-on and NEVER shows a
  // "connecting..." screen. A screen that sits on one message is what made a
  // 3-second delay look like a crash.
  drawScreen("Souly", "", "Starting...", "");

#if USE_RFID
  SPI.begin(RC_SCK, RC_MISO, RC_MOSI, RC_SS);
  rfid.PCD_Init();
  rfid.PCD_AntennaOn();
  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.printf("RC522 version 0x%02X %s\n", v,
                (v == 0x91 || v == 0x92) ? "(ok)" : "(NOT RESPONDING)");
#else
  Serial.println(F("RFID disabled — BOOT button acts as the card"));
#endif

  memset(&box, 0, sizeof(box));
  box.backlight = true;
  boxLock  = xSemaphoreCreateMutex();
  tapQueue = xQueueCreate(4, sizeof(TapMsg));

  // Core 0. The Arduino loop runs on core 1, so the network cannot stall the
  // display no matter how badly HTTPClient misbehaves.
  xTaskCreatePinnedToCore(netTask, "net", 10240, nullptr, 1, nullptr, 0);
}


void loop() {
  unsigned long now = millis();

  ledService();

  /* --- card ------------------------------------------------------------- */
  static unsigned long lastScan = 0;
  if (now - lastScan >= CARD_SCAN_MS) {
    lastScan = now;
    rfidWatchdog();
    char uid[24];
    if (readCard(uid, sizeof(uid))) {
      TapMsg m; strncpy(m.uid, uid, sizeof(m.uid) - 1); m.uid[sizeof(m.uid)-1] = '\0';
      xQueueSend(tapQueue, &m, 0);
      ledBlink(1, false);              // immediate local acknowledgement, so
                                       // the tap feels registered before the
                                       // server has even been asked
    }
  }

  /* --- drain the mailbox ------------------------------------------------- */
  char rows[LCD_ROWS][LCD_COLS + 1];
  bool haveRows = false, haveBl = false, bl = true;
  int  blinks = 0; bool pulse = false;
  uint32_t hold = 0;

  if (xSemaphoreTake(boxLock, pdMS_TO_TICKS(5)) == pdTRUE) {
    if (box.linesFresh) {
      for (int i = 0; i < LCD_ROWS; i++) strcpy(rows[i], box.lines[i]);
      hold = box.holdMs;
      box.linesFresh = false;
      haveRows = true;
    }
    if (box.ledFresh)       { blinks = box.ledCount; pulse = box.ledPulse; box.ledFresh = false; }
    if (box.backlightFresh) { bl = box.backlight; haveBl = true; box.backlightFresh = false; }
    xSemaphoreGive(boxLock);
  }

  if (blinks) ledBlink(blinks, pulse);
  if (haveBl && lcd) { bl ? lcd->backlight() : lcd->noBacklight(); }

  if (haveRows) {
    // While a temporary screen is up — a flag, a welcome, a denial — a routine
    // poll result must not overwrite it. hold_ms comes from the server, so one
    // mechanic covers all four cases.
    bool holding = holdFor && (now - holdStart < holdFor);
    if (!holding || hold) {
      drawScreen(rows[0], rows[1], rows[2], rows[3]);
      holdStart = now;
      holdFor   = hold;
    }
  }

  delay(5);          // yields to the idle task and feeds the watchdog
}
