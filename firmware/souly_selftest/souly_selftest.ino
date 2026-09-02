/* ============================================================================
   SOULY — classroom device.  Self-test, then live.

   ONE sketch. Upload it once.

   It runs seven checks in the order things actually depend on each other,
   reports all of them on one screen, and — if they all pass — drops straight
   into the real device behaviour without another upload.

     1 LCD    I2C bus, and something answering on it
     2 LED    three blinks you confirm with your eyes
     3 RFID   the reader's version register
     4 CARD   an actual UID from an actual tap
     5 WIFI   association, with the disconnect reason if it fails
     6 TCP    a raw socket to the server, timed
     7 API    POST /hello returns 200 and parses

   It does NOT stop at the first failure. A failed check marks its dependents
   as skipped and the run continues, so one upload tells you everything that
   is wrong rather than the first thing that is wrong.

   The report card, on the 20x4:

       1 LCD  OK  5 WIFI OK
       2 LED  OK  6 TCP  OK
       3 RFID OK  7 API  OK
       4 CARD OK  ALL PASS

   ---------------------------------------------------------------------------
   WHY CHECK 1 ALSO TALKS TO THE LED
   ---------------------------------------------------------------------------
   If the screen is the broken thing, the screen cannot tell you so. Every
   check therefore reports three ways: on the LCD, over serial, and as a
   blink count on the LED. A board with a dead display and a dead serial port
   still blinks the number of the check that failed.

   ---------------------------------------------------------------------------
   THE SCREEN IS RENDERED ON THE SERVER
   ---------------------------------------------------------------------------
   In live mode every endpoint returns four strings already padded to 20
   columns, plus what the lamp should do. This sketch prints them. It does not
   decide what they say.

   Everything that can be WRONG — session timing, which flag to show, how to
   abbreviate a name, when to suppress a repeat — lives in Python, in
   app/routers/device.py, where it is testable and changeable without a
   reflash. Tuning the flag cooldown the night before a competition should be
   an edit and a restart.

   ---------------------------------------------------------------------------
   WIRING                        (verified — do not re-derive)
   ---------------------------------------------------------------------------
     RC522  SDA/SS -> 10    SCK -> 12   MOSI -> 11   MISO -> 13   RST -> 9
            VCC    -> 3.3V  ** never 5V **
     LCD    SDA    -> 4     SCL -> 5    VCC  -> 5V
     LED    GPIO 1 + 220-330R -> GND

   This is an R8 part with 8MB octal PSRAM, so GPIO 33-37 belong to the PSRAM
   and cannot be used for anything. The LCD is a 2004A: 20 columns, not 16.
   The RC522 on 5V still lights its power LED and still measures 3.3V at the
   pins while its digital side is dead — which looks exactly like a wiring
   fault and is not one.

   Arduino IDE: board "ESP32S3 Dev Module", USB CDC On Boot = ENABLED.
   Without that last one Serial goes to GPIO 43/44 and the monitor stays
   blank forever.

   Libraries: LiquidCrystal_I2C (Frank de Brabander), MFRC522 (GithubCommunity
   1.4.x — NOT MFRC522v2), ArduinoJson 7.x.
   ============================================================================ */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

#include "secrets.h"      // WIFI_SSID, WIFI_PASS, SERVER_HOST, SERVER_PORT, DEVICE_KEY


/* ---------------------------------------------------------------------------
   Pins
   --------------------------------------------------------------------------- */
#define PIN_LED   1
#define PIN_BOOT  0        // the BOOT button, used to skip the card check
#define PIN_SDA   4
#define PIN_SCL   5

#define RC_SS     10
#define RC_RST    9
#define RC_SCK    12
#define RC_MOSI   11
#define RC_MISO   13

#define LCD_COLS  20
#define LCD_ROWS  4

/* Timings. The two HTTP ones are the important ones: no call may ever block
   the loop for longer than this, because a blocked loop is a device that
   looks frozen — which is exactly how this went wrong the first time. */
const uint32_t HTTP_CONNECT_MS = 800;
const uint32_t HTTP_READ_MS    = 800;
const uint32_t TCP_PROBE_MS    = 3000;   // self-test only, wants to be generous

const uint32_t POLL_SESSION_MS = 1000;   // during a lesson
const uint32_t POLL_IDLE_MS    = 3000;   // nothing is happening; nothing changes
const uint32_t CARD_REPEAT_MS  = 3000;   // ignore the same card for this long
const uint32_t CARD_WAIT_MS    = 30000;  // how long check 4 waits for a tap


/* ---------------------------------------------------------------------------
   Globals
   --------------------------------------------------------------------------- */
LiquidCrystal_I2C *lcd = nullptr;        // built after we find its address
MFRC522 rfid(RC_SS, RC_RST);
uint8_t lcdAddr = 0;

// What is currently on each row. An HD44780 is slow over I2C — a full repaint
// is roughly 100ms — so redrawing all four rows every second would flicker
// visibly for a whole lesson. We compare and write only what changed.
String shown[LCD_ROWS] = {"", "", "", ""};


/* ---------------------------------------------------------------------------
   The report card
   --------------------------------------------------------------------------- */
enum Result { R_PENDING, R_PASS, R_FAIL, R_WARN, R_SKIP };

struct Check {
  const char *name;      // 4 chars max — it has to fit the report card
  Result      result;
  String      detail;    // the sentence printed to serial
};

Check checks[7] = {
  {"LCD",  R_PENDING, ""},
  {"LED",  R_PENDING, ""},
  {"RFID", R_PENDING, ""},
  {"CARD", R_PENDING, ""},
  {"WIFI", R_PENDING, ""},
  {"TCP",  R_PENDING, ""},
  {"API",  R_PENDING, ""},
};

const char *mark(Result r) {
  switch (r) {
    case R_PASS: return "OK";
    case R_FAIL: return "--";
    case R_WARN: return "??";
    case R_SKIP: return "sk";
    default:     return "..";
  }
}

void setCheck(int i, Result r, const String &detail) {
  checks[i].result = r;
  checks[i].detail = detail;
  const char *word = r == R_PASS ? "PASS" : r == R_FAIL ? "FAIL"
                   : r == R_WARN ? "WARN" : "SKIP";
  Serial.printf("  [%d %s] %s — %s\n", i + 1, checks[i].name, word, detail.c_str());
}


/* ---------------------------------------------------------------------------
   LED
   ---------------------------------------------------------------------------
   Blocking blinks are fine during the self-test, where nothing else is
   happening. They are NOT fine in live mode: three blinks at 200ms is 1.2
   seconds during which the card reader is deaf and the screen is frozen —
   a self-inflicted freeze indistinguishable from a crash. Live mode uses the
   non-blocking version further down.
   --------------------------------------------------------------------------- */
void blinkBlocking(int times, int onMs = 180, int offMs = 220) {
  for (int i = 0; i < times; i++) {
    digitalWrite(PIN_LED, HIGH); delay(onMs);
    digitalWrite(PIN_LED, LOW);  delay(offMs);
  }
}

struct {
  int           remaining = 0;
  bool          on        = false;
  unsigned long next      = 0;
  bool          pulse     = false;
} led;

void ledBlink(int times, bool asPulse = false) {
  led.remaining = times * 2;          // on and off for each blink
  led.on        = false;
  led.pulse     = asPulse;
  led.next      = 0;                  // fire immediately
}

void ledService() {
  if (led.remaining <= 0) return;
  if (millis() < led.next) return;

  led.on = !led.on;
  digitalWrite(PIN_LED, led.on ? HIGH : LOW);
  led.remaining--;
  // A pulse is slow and even; a blink is quick. The teacher reads the rhythm
  // before they read the screen, so "the room is drifting" and "this one
  // child" should not feel the same.
  led.next = millis() + (led.pulse ? 600 : (led.on ? 180 : 220));

  if (led.remaining <= 0) digitalWrite(PIN_LED, LOW);
}


/* ---------------------------------------------------------------------------
   Screen
   --------------------------------------------------------------------------- */
String padTo(const String &s, int cols) {
  String out = s.substring(0, cols);
  while ((int)out.length() < cols) out += ' ';
  return out;
}

/* Writes a row only if it actually differs from what is already up there. */
void drawRow(int row, const String &text) {
  if (!lcd || row < 0 || row >= LCD_ROWS) return;
  String line = padTo(text, LCD_COLS);
  if (line == shown[row]) return;
  lcd->setCursor(0, row);
  lcd->print(line);
  shown[row] = line;
}

void drawScreen(const String &a, const String &b = "",
                const String &c = "", const String &d = "") {
  drawRow(0, a); drawRow(1, b); drawRow(2, c); drawRow(3, d);
}

void banner(const String &a, const String &b = "",
            const String &c = "", const String &d = "") {
  drawScreen(a, b, c, d);
  Serial.printf("| %s\n", a.c_str());
  if (b.length()) Serial.printf("| %s\n", b.c_str());
  if (c.length()) Serial.printf("| %s\n", c.c_str());
  if (d.length()) Serial.printf("| %s\n", d.c_str());
}


/* ===========================================================================
   CHECK 1 — I2C and the LCD
   ---------------------------------------------------------------------------
   Scans the bus rather than assuming an address. These backpacks ship as
   either 0x27 (PCF8574) or 0x3F (PCF8574A) and the two are indistinguishable
   from the outside.
   =========================================================================== */
void check1_lcd() {
  Serial.println(F("\n[1] I2C / LCD"));
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);

  int found = 0;
  String addrs = "";
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      found++;
      char buf[8]; snprintf(buf, sizeof(buf), "0x%02X ", a);
      addrs += buf;
      if (a == 0x27 || a == 0x3F) lcdAddr = a;
      if (!lcdAddr) lcdAddr = a;      // something is there; try it anyway
    }
  }

  if (!found) {
    setCheck(0, R_FAIL,
             "nothing on the I2C bus. SDA->4, SCL->5, and LCD VCC to 5V. "
             "Check the backpack is soldered on.");
    return;
  }

  lcd = new LiquidCrystal_I2C(lcdAddr, LCD_COLS, LCD_ROWS);
  lcd->init();
  lcd->backlight();
  lcd->clear();

  char buf[48];
  snprintf(buf, sizeof(buf), "found %s(using 0x%02X)", addrs.c_str(), lcdAddr);
  setCheck(0, R_PASS, buf);

  banner("SOULY self-test", "", "1 LCD  OK", "starting...");
  delay(900);
}


/* ===========================================================================
   CHECK 2 — the LED
   ---------------------------------------------------------------------------
   The only check that cannot verify itself. It blinks; you look.
   =========================================================================== */
void check2_led() {
  Serial.println(F("\n[2] LED"));
  banner("2 LED", "", "watch the lamp:", "3 slow blinks");
  delay(600);
  blinkBlocking(3, 300, 300);
  setCheck(1, R_PASS,
           "blinked 3x. If you saw nothing: GPIO 1, the resistor, "
           "or the LED is in backwards.");
  delay(400);
}


/* ===========================================================================
   CHECK 3 — is the RC522 on the bus at all
   ---------------------------------------------------------------------------
   The single most useful reading on this whole board. 0x91 or 0x92 means
   SCK, MOSI, MISO, SS and RST are ALL correct simultaneously — one register
   read settles five wires at once. 0x00 or 0xFF means the reader is not
   talking, and no amount of tapping cards will change that.
   =========================================================================== */
void check3_rfid() {
  Serial.println(F("\n[3] RC522"));
  banner("3 RFID", "", "reading version", "register...");

  SPI.begin(RC_SCK, RC_MISO, RC_MOSI, RC_SS);
  rfid.PCD_Init();
  delay(50);

  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  char buf[80];

  if (v == 0x91 || v == 0x92) {
    snprintf(buf, sizeof(buf), "version 0x%02X — v%d.0, all five SPI wires good",
             v, v == 0x91 ? 1 : 2);
    setCheck(2, R_PASS, buf);
  } else if (v == 0x00 || v == 0xFF) {
    snprintf(buf, sizeof(buf),
             "version 0x%02X — reader not on the bus. Check SS=10 RST=9 "
             "SCK=12 MOSI=11 MISO=13, and that VCC is 3.3V NOT 5V.", v);
    setCheck(2, R_FAIL, buf);
  } else {
    snprintf(buf, sizeof(buf),
             "version 0x%02X — unexpected but it IS answering, so continuing", v);
    setCheck(2, R_WARN, buf);
  }
  delay(400);
}


/* ===========================================================================
   CHECK 4 — an actual card
   ---------------------------------------------------------------------------
   Needs a human, so it can be skipped with the BOOT button. Skipping marks
   it, it does not fail it — a skipped check is an honest "we don't know".
   =========================================================================== */
String uidToHex(MFRC522::Uid &uid) {
  String s = "";
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) s += '0';
    s += String(uid.uidByte[i], HEX);
  }
  s.toUpperCase();
  return s;
}

void check4_card() {
  Serial.println(F("\n[4] card read"));

  if (checks[2].result == R_FAIL) {
    setCheck(3, R_SKIP, "the reader never answered, so there is nothing to tap onto");
    return;
  }

  Serial.println(F("    tap a card (BOOT button skips)"));
  unsigned long deadline = millis() + CARD_WAIT_MS;
  int lastSecond = -1;

  while (millis() < deadline) {
    if (digitalRead(PIN_BOOT) == LOW) {
      setCheck(3, R_SKIP, "skipped with the BOOT button");
      delay(500);
      return;
    }

    int left = (deadline - millis()) / 1000;
    if (left != lastSecond) {
      lastSecond = left;
      banner("4 CARD", "Tap a card now", "BOOT to skip",
             String(left) + "s left");
    }

    if (rfid.PICC_IsNewCardPresent() && rfid.PICC_ReadCardSerial()) {
      String uid = uidToHex(rfid.uid);
      rfid.PICC_HaltA();
      rfid.PCD_StopCrypto1();

      ledBlink(1);
      setCheck(3, R_PASS, "UID " + uid);
      banner("4 CARD  OK", "", uid, "");
      // 62EA2F03 is Sarah Ahmed's card, and she teaches P5 Mathematics —
      // which is the class this device is bound to.
      Serial.printf("    -> enrol an unknown card with:\n"
                    "       python scripts/seed_classes.py --card sarah=%s\n",
                    uid.c_str());
      delay(1500);
      return;
    }
    delay(40);
  }

  setCheck(3, R_WARN, "no card presented within 30s");
}


/* ===========================================================================
   CHECK 5 — WiFi
   ---------------------------------------------------------------------------
   The order of operations below is the whole check. The original firmware
   called WiFi.begin() again on every loop while the previous attempt was
   still running, and scanned the air mid-connect. ESP-IDF refuses a new
   config while the station is connecting:

       E (28957) wifi:sta is connecting, cannot set config

   ...so the second call was discarded and the first was left wedged. The
   radio never got one clean run at it. Signal was never the problem.
   =========================================================================== */
int lastDisconnectReason = 0;

const char *reasonText(int r) {
  switch (r) {
    // Reason 2 is the classic iPhone-hotspot answer. iOS puts the hotspot to
    // sleep when nothing is attached and manages clients aggressively, so the
    // association expires mid-handshake. Keep the Personal Hotspot screen OPEN
    // on the phone while the device connects.
    case 2:   return "AUTH EXPIRED -> often an iPhone hotspot sleeping. "
                     "Keep the Personal Hotspot screen open, and turn ON "
                     "'Maximise Compatibility' to force 2.4GHz";
    case 15:  return "4-WAY HANDSHAKE TIMEOUT -> wrong password";
    case 201: return "NO AP FOUND -> wrong SSID, or it is 5GHz only";
    case 202: return "AUTH FAILED -> password, or the AP refused us";
    case 203: return "ASSOC FAILED -> AP refused, maybe full";
    case 204: return "HANDSHAKE TIMEOUT -> weak link, or WPA3/PMF mismatch";
    default:  return "see esp_wifi_types.h";
  }
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    lastDisconnectReason = info.wifi_sta_disconnected.reason;
  }
}

/* ---------------------------------------------------------------------------
   TX POWER — the thing that actually breaks these boards
   ---------------------------------------------------------------------------
   Cheap ESP32-S3 modules ship with poor RF matching. At the default 19.5 dBm
   the transmit bursts distort badly enough that the WPA2 four-way handshake
   frames arrive corrupted, so the AP never completes authentication and the
   association expires: reason 2, AUTH_EXPIRE. Turning the power DOWN makes
   the signal cleaner and the handshake succeeds.

   It is counterintuitive — less power, better connection — and it explains
   two things that made no sense: an excellent RSSI (that is RECEIVE strength,
   which says nothing about how cleanly this board TRANSMITS), and failing on
   two completely different networks.

   Rather than guess a value, sweep them. The first that associates wins, and
   the report card says which one it was.

   Widely reported as the fix for exactly this symptom on ESP32-S3-DevKitC and
   N16R8 boards. WiFi.setTxPower() must be called AFTER WiFi.begin().
   --------------------------------------------------------------------------- */
struct PowerStep { wifi_power_t value; const char *label; };

PowerStep powerLadder[] = {
  {WIFI_POWER_19_5dBm, "19.5 dBm (default)"},
  {WIFI_POWER_11dBm,   "11 dBm"},
  {WIFI_POWER_8_5dBm,  "8.5 dBm"},
  {WIFI_POWER_5dBm,    "5 dBm"},
};
const int POWER_STEPS = sizeof(powerLadder) / sizeof(powerLadder[0]);

int chosenPower = -1;   // index into powerLadder, set once something works

bool wifiConnectAt(uint32_t budgetMs, int step) {
  WiFi.persistent(false);
  WiFi.mode(WIFI_OFF);            // tear down anything still half-alive
  delay(300);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);           // power saving mid-handshake loses packets
  WiFi.disconnect(true, true);
  delay(300);

  WiFi.begin(WIFI_SSID, WIFI_PASS);   // exactly once, then leave it alone
  WiFi.setTxPower(powerLadder[step].value);   // must come after begin()

  Serial.printf("    trying at %s ", powerLadder[step].label);

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < budgetMs) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

/* Walks down the ladder until one sticks. */
bool wifiConnect(uint32_t budgetPerStepMs) {
  for (int i = 0; i < POWER_STEPS; i++) {
    if (wifiConnectAt(budgetPerStepMs, i)) {
      chosenPower = i;
      Serial.printf("    -> associated at %s\n", powerLadder[i].label);
      if (i > 0) {
        Serial.println(F("    NOTE: the default power failed and a lower one"));
        Serial.println(F("    worked. That is this board's RF front end, not"));
        Serial.println(F("    your router and not your password."));
      }
      return true;
    }
    Serial.printf("    failed at %s (reason %d - %s)\n",
                  powerLadder[i].label, lastDisconnectReason,
                  reasonText(lastDisconnectReason));
  }
  return false;
}

/* Is the SSID physically on the air?

   This scan happens ONCE, before any connect attempt — scanning while the
   station is connecting is half of what caused the original deadlock.

   It exists because "not connected" has two completely different causes that
   produce the same symptom: the network is not there at all (an iPhone
   hotspot running 5GHz-only, which an ESP32 has no radio for), or the name in
   secrets.h does not match the name being broadcast byte for byte. The second
   one is nastier than it sounds: iOS names devices with a CURLY apostrophe
   (U+2019, three bytes in UTF-8), and a straight ' typed into secrets.h is a
   different string entirely. */
bool ssidVisible() {
  Serial.printf("    scanning for '%s' (%d bytes)...\n",
                WIFI_SSID, (int)strlen(WIFI_SSID));

  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(200);

  int n = WiFi.scanNetworks();
  bool found = false;
  Serial.printf("    %d networks on the air:\n", n);
  for (int i = 0; i < n; i++) {
    bool mine = (WiFi.SSID(i) == WIFI_SSID);
    if (mine) found = true;
    Serial.printf("      %-32s %4d dBm  ch%-3d %s\n",
                  WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i),
                  mine ? "  <-- ours" : "");
  }
  WiFi.scanDelete();

  if (!found) {
    Serial.println(F("    !! that SSID is NOT being broadcast on 2.4GHz."));
    Serial.println(F("       iPhone hotspot: turn ON 'Maximise Compatibility'"));
    Serial.println(F("       (Settings > Personal Hotspot). An ESP32 has no"));
    Serial.println(F("       5GHz radio at all. Also check the name matches"));
    Serial.println(F("       exactly — iOS uses a curly apostrophe."));
  }
  return found;
}

void check5_wifi() {
  Serial.println(F("\n[5] WiFi"));
  banner("5 WIFI", WIFI_SSID, "scanning...", "");

  WiFi.onEvent(onWiFiEvent);
  bool visible = ssidVisible();

  banner("5 WIFI", WIFI_SSID,
         visible ? "on the air" : "NOT on the air", "connecting...");

  if (!wifiConnect(9000)) {          // 9s x 4 power steps
    char buf[220];
    if (!visible) {
      snprintf(buf, sizeof(buf),
               "'%s' is not being broadcast on 2.4GHz. iPhone hotspot -> turn "
               "ON 'Maximise Compatibility'; an ESP32 has no 5GHz radio. Also "
               "check the SSID matches byte-for-byte (iOS uses a curly '). "
               "Last reason %d — %s", WIFI_SSID, lastDisconnectReason,
               reasonText(lastDisconnectReason));
    } else {
      snprintf(buf, sizeof(buf), "visible but would not join. reason %d — %s",
               lastDisconnectReason, reasonText(lastDisconnectReason));
    }
    setCheck(4, R_FAIL, buf);
    banner("5 WIFI --", WIFI_SSID,
           "reason " + String(lastDisconnectReason), "see serial monitor");
    delay(2500);
    return;
  }

  char buf[200];
  snprintf(buf, sizeof(buf), "ip %s  gw %s  rssi %d dBm  ch%d  TX POWER %s",
           WiFi.localIP().toString().c_str(),
           WiFi.gatewayIP().toString().c_str(),
           WiFi.RSSI(), WiFi.channel(),
           powerLadder[chosenPower].label);
  setCheck(4, R_PASS, buf);

  // If a reduced power was needed, say so on the screen too — it is the one
  // finding worth carrying into the final firmware.
  banner("5 WIFI  OK", WiFi.localIP().toString(),
         chosenPower > 0 ? String("TX ") + powerLadder[chosenPower].label
                         : String("gw ") + WiFi.gatewayIP().toString(),
         String(WiFi.RSSI()) + " dBm");
  delay(1600);
}


/* ===========================================================================
   CHECK 6 — a raw socket to the server
   ---------------------------------------------------------------------------
   No HTTPClient here on purpose. This is the one measurement that separates
   two problems which produce identical-looking symptoms:
     timed out  -> the packets went into a void: a firewall DROPPING them,
                   or the wrong IP
     refused    -> something answered "nothing is listening": uvicorn is off,
                   or bound to 127.0.0.1 instead of 0.0.0.0
   A firewall that refuses answers instantly. One that drops makes you wait
   the full timeout. The elapsed time IS the diagnosis.
   =========================================================================== */
void check6_tcp() {
  Serial.println(F("\n[6] TCP to the server"));

  if (checks[4].result != R_PASS) {
    setCheck(5, R_SKIP, "no network");
    return;
  }

  banner("6 TCP", String(SERVER_HOST) + ":" + String(SERVER_PORT),
         "connecting...", "");

  IPAddress ip;
  if (!ip.fromString(SERVER_HOST)) {
    setCheck(5, R_FAIL, "SERVER_HOST in secrets.h is not a valid IP address");
    return;
  }

  // A device on 192.168.8.x cannot reach 192.168.1.x, and no firewall rule
  // will ever change that. Worth ruling out before blaming Windows.
  IPAddress me = WiFi.localIP(), mask = WiFi.subnetMask();
  bool sameSubnet = true;
  for (int i = 0; i < 4; i++)
    if ((me[i] & mask[i]) != (ip[i] & mask[i])) sameSubnet = false;

  if (!sameSubnet) {
    setCheck(5, R_FAIL,
             "the laptop is on a DIFFERENT network from the device. "
             "Run ipconfig and put the right IP in secrets.h.");
    banner("6 TCP  --", "different networks", me.toString(), ip.toString());
    delay(2500);
    return;
  }

  WiFiClient c;
  unsigned long t0 = millis();
  bool ok = c.connect(ip, SERVER_PORT, TCP_PROBE_MS);
  unsigned long took = millis() - t0;
  c.stop();

  char buf[160];
  if (ok) {
    snprintf(buf, sizeof(buf), "connected in %lums — the network is fine", took);
    setCheck(5, R_PASS, buf);
    banner("6 TCP   OK", String(SERVER_HOST) + ":" + String(SERVER_PORT),
           String(took) + " ms", "");
  } else if (took >= TCP_PROBE_MS - 200) {
    snprintf(buf, sizeof(buf),
             "timed out after %lums — packets went nowhere. Windows Firewall "
             "is DROPPING them, or the IP is wrong. Allow python.exe on "
             "Private networks, or add an inbound rule for TCP 8000.", took);
    setCheck(5, R_FAIL, buf);
    banner("6 TCP   --", "timed out", "firewall is blocking", "or wrong IP");
  } else {
    snprintf(buf, sizeof(buf),
             "refused after %lums — something said no. uvicorn is not running, "
             "or it bound to 127.0.0.1. Start it with run.bat.", took);
    setCheck(5, R_FAIL, buf);
    banner("6 TCP   --", "refused", "server is not", "running. run.bat");
  }
  delay(2000);
}


/* ===========================================================================
   HTTP — used by check 7 and by live mode
   ---------------------------------------------------------------------------
   Short, hard ceilings on both phases. A dropped packet on a MiFi is normal
   and the next poll is a second away; what is NOT acceptable is a call that
   blocks the loop long enough to look like a crash.

   setReuse is deliberately off. A kept-alive socket that the server has
   since closed is a stall waiting to happen, and it stalls inside begin(),
   before any timeout applies.
   =========================================================================== */
int lastHttpCode = 0;

String httpCall(const char *method, const char *path, const String &body = "") {
  if (WiFi.status() != WL_CONNECTED) { lastHttpCode = -100; return ""; }

  WiFiClient net;
  HTTPClient http;

  net.setTimeout(1);                       // seconds — belt and braces
  String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT + path;

  if (!http.begin(net, url)) { lastHttpCode = -101; return ""; }

  http.setConnectTimeout(HTTP_CONNECT_MS);
  http.setTimeout(HTTP_READ_MS);
  http.setReuse(false);
  http.addHeader("X-Souly-Device", DEVICE_KEY);

  int code;
  if (strcmp(method, "POST") == 0) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body.length() ? body : "{}");
  } else {
    code = http.GET();
  }

  lastHttpCode = code;
  String out = (code == 200) ? http.getString() : "";
  http.end();
  return out;
}


/* ===========================================================================
   CHECK 7 — the device key and the contract
   =========================================================================== */
String helloBody = "";

void check7_api() {
  Serial.println(F("\n[7] POST /api/device/hello"));

  if (checks[5].result != R_PASS) {
    setCheck(6, R_SKIP, "could not reach the server");
    return;
  }

  banner("7 API", "POST /hello", "", "");

  String body = httpCall("POST", "/api/device/hello", "{\"firmware\":\"selftest-1\"}");

  if (lastHttpCode == 401) {
    setCheck(6, R_FAIL,
             "401 — the server does not recognise this DEVICE_KEY. Run "
             "python scripts/seed_classes.py and copy the key it prints "
             "into secrets.h.");
    banner("7 API   --", "device key", "rejected (401)", "re-seed & reflash");
    delay(2500);
    return;
  }
  if (lastHttpCode != 200) {
    setCheck(6, R_FAIL, "HTTP " + String(lastHttpCode) +
                        " — the server answered, but not with 200");
    banner("7 API   --", "HTTP " + String(lastHttpCode), "", "");
    delay(2500);
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    setCheck(6, R_FAIL, "200, but the body did not parse as JSON");
    return;
  }

  const char *label = doc["device"]  | "?";
  const char *cls   = doc["class_name"] | "(none)";
  bool inSession    = doc["in_session"] | false;
  int  cols         = doc["lcd_cols"] | 20;

  char buf[160];
  snprintf(buf, sizeof(buf), "200 — device '%s', class '%s', %d cols, %s",
           label, cls, cols, inSession ? "a lesson is ALREADY OPEN" : "no lesson open");
  setCheck(6, R_PASS, buf);

  helloBody = body;
  banner("7 API   OK", label, cls, inSession ? "lesson already open" : "no lesson open");
  delay(1500);
}


/* ===========================================================================
   The report card
   =========================================================================== */
bool allGreen() {
  for (int i = 0; i < 7; i++)
    if (checks[i].result != R_PASS) return false;
  return true;
}

int firstProblem() {
  for (int i = 0; i < 7; i++)
    if (checks[i].result == R_FAIL) return i + 1;
  for (int i = 0; i < 7; i++)
    if (checks[i].result != R_PASS) return i + 1;
  return 0;
}

void reportCard() {
  Serial.println(F("\n========================================"));
  Serial.println(F("  REPORT"));
  Serial.println(F("========================================"));
  for (int i = 0; i < 7; i++)
    Serial.printf("  %d %-5s %-4s  %s\n", i + 1, checks[i].name,
                  mark(checks[i].result), checks[i].detail.c_str());

  char r0[24], r1[24], r2[24], r3[24];
  snprintf(r0, sizeof(r0), "1 %-4s %-2s  5 %-4s %-2s",
           checks[0].name, mark(checks[0].result), checks[4].name, mark(checks[4].result));
  snprintf(r1, sizeof(r1), "2 %-4s %-2s  6 %-4s %-2s",
           checks[1].name, mark(checks[1].result), checks[5].name, mark(checks[5].result));
  snprintf(r2, sizeof(r2), "3 %-4s %-2s  7 %-4s %-2s",
           checks[2].name, mark(checks[2].result), checks[6].name, mark(checks[6].result));

  int bad = firstProblem();
  char verdict[12];
  if (bad == 0) snprintf(verdict, sizeof(verdict), "ALL PASS");
  else          snprintf(verdict, sizeof(verdict), "SEE #%d", bad);
  snprintf(r3, sizeof(r3), "4 %-4s %-2s  %-8s",
           checks[3].name, mark(checks[3].result), verdict);

  drawScreen(r0, r1, r2, r3);
  Serial.printf("\n  %s\n\n", bad == 0 ? "ALL PASS — going live."
                                       : "Fix the first FAIL, then re-upload.");

  // Blink the number of the first problem, so a board with a dead screen and
  // a dead serial port can still tell you where to look.
  if (bad) { delay(800); blinkBlocking(bad, 250, 250); }
}


/* ===========================================================================
   LIVE MODE
   ---------------------------------------------------------------------------
   Four states, one display mechanic.

     IDLE      no lesson open        screen from the server
     LESSON    a lesson is running   screen from the server
     HOLDING   showing something for hold_ms, then going back

   HOLDING covers every temporary screen — a flag, a welcome, a denial, a
   session ending. They are all "show these four lines for hold_ms, then
   resume", and the server sends hold_ms on every response. One mechanic
   instead of four is one thing to get wrong instead of four.
   =========================================================================== */
unsigned long holdUntil    = 0;
unsigned long nextPoll     = 0;
bool          inSession    = false;

String        lastCardUid  = "";
unsigned long lastCardMs   = 0;
int           serverMisses = 0;

/* Draws whatever the server just sent, and obeys the lamp instruction. */
void renderResponse(const String &json) {
  if (!json.length()) return;

  JsonDocument doc;
  if (deserializeJson(doc, json)) {
    Serial.println(F("bad JSON from server"));
    return;
  }

  JsonArray lines = doc["lines"];
  if (!lines.isNull()) {
    for (int i = 0; i < LCD_ROWS && i < (int)lines.size(); i++)
      drawRow(i, String(lines[i].as<const char *>()));
  }

  const char *state = doc["state"] | "";
  inSession = (strcmp(state, "session") == 0 ||
               strcmp(state, "flag")    == 0 ||
               strcmp(state, "flag_room") == 0);

  if (doc["backlight"].is<bool>() && lcd) {
    doc["backlight"].as<bool>() ? lcd->backlight() : lcd->noBacklight();
  }

  const char *pattern = doc["led"]["pattern"] | "none";
  int count           = doc["led"]["count"]   | 0;
  if (count > 0 && strcmp(pattern, "none") != 0)
    ledBlink(count, strcmp(pattern, "pulse") == 0);

  uint32_t hold = doc["hold_ms"] | 0;
  if (hold) holdUntil = millis() + hold;

  // Tell the server what actually reached the teacher's eyes. Doing this here
  // rather than inside /poll means a flag lost to a dropped packet gets shown
  // on the next poll instead of being silently swallowed.
  JsonArray ids = doc["flag_ids"];
  if (!ids.isNull() && ids.size() > 0) {
    String payload = "{\"flag_ids\":[";
    for (size_t i = 0; i < ids.size(); i++) {
      if (i) payload += ',';
      payload += String(ids[i].as<int>());
    }
    payload += "]}";
    httpCall("POST", "/api/device/shown", payload);
  }
}

/* A tap must always answer. A poll must always stay quiet.

   These are not the same rule. A failed poll changes nothing — the teacher
   never knows, and the next one is a second away. A failed TAP has to say so,
   because a teacher who taps and sees nothing taps harder, then assumes the
   device is broken. */
void sendTap(const String &uid) {
  Serial.printf("tap %s\n", uid.c_str());
  String out = httpCall("POST", "/api/device/tap",
                        "{\"card_uid\":\"" + uid + "\"}");

  if (!out.length()) {
    drawScreen("No server", "", "Try again", "");
    ledBlink(3);
    holdUntil = millis() + 2000;
    return;
  }
  renderResponse(out);
}

void readCard() {
  if (!rfid.PICC_IsNewCardPresent()) return;
  if (!rfid.PICC_ReadCardSerial())   return;

  String uid = uidToHex(rfid.uid);
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  // The RC522 reports the same card continuously while it sits in the field.
  // Unguarded, one physical tap becomes start / end / start about twenty
  // times a second, and the demo dies in front of a judge.
  if (uid == lastCardUid && millis() - lastCardMs < CARD_REPEAT_MS) return;

  lastCardUid = uid;
  lastCardMs  = millis();
  sendTap(uid);
}

void pollServer() {
  String out = httpCall("GET", "/api/device/poll");
  if (!out.length()) {
    serverMisses++;
    // Deliberately silent. The screen keeps whatever it had. Ten consecutive
    // misses is a real outage and worth saying; one is just a MiFi.
    if (serverMisses == 10)
      drawScreen("No reply from",
                 String(SERVER_HOST) + ":" + String(SERVER_PORT),
                 "Server off, or the", "firewall is blocking");
    return;
  }
  serverMisses = 0;
  renderResponse(out);
}

void liveLoop() {
  ledService();

  static unsigned long nextCard = 0;
  if (millis() >= nextCard) {
    nextCard = millis() + 50;
    if (checks[2].result != R_FAIL) readCard();
  }

  if (millis() < holdUntil) return;      // a temporary screen is up; leave it

  if (millis() >= nextPoll) {
    nextPoll = millis() + (inSession ? POLL_SESSION_MS : POLL_IDLE_MS);
    pollServer();
  }
}


/* ===========================================================================
   setup / loop
   =========================================================================== */
bool live = false;

void setup() {
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  pinMode(PIN_BOOT, INPUT_PULLUP);

  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 2000) delay(10);
  delay(300);

  Serial.println(F("\n\n========================================"));
  Serial.println(F("  SOULY classroom device — self-test"));
  Serial.println(F("========================================"));
  Serial.printf("  wifi    %s\n", WIFI_SSID);
  Serial.printf("  server  %s:%d\n", SERVER_HOST, SERVER_PORT);

  check1_lcd();
  check2_led();
  check3_rfid();
  check4_card();
  check5_wifi();
  check6_tcp();
  check7_api();

  reportCard();

  // A green report card IS a working device, so it goes straight to work.
  // CARD warning only means nobody tapped during the test — not a fault.
  bool blocking = checks[0].result == R_FAIL || checks[4].result == R_FAIL ||
                  checks[5].result == R_FAIL || checks[6].result == R_FAIL;

  if (!blocking) {
    delay(2500);
    live = true;
    Serial.println(F("\n--- LIVE ---\n"));
    if (helloBody.length()) renderResponse(helloBody);
    nextPoll = millis();
  } else {
    Serial.println(F("\nStaying on the report card. Fix, then re-upload."));
  }
}

void loop() {
  if (!live) { delay(200); return; }   // report card stays up, unchanged
  liveLoop();
  delay(5);
}
