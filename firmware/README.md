# Firmware — the classroom device

ESP32-S3 · MFRC522 RFID · 20×4 I²C LCD · status LED.

```
souly_device/     the real firmware. Flash this to run the device.
souly_selftest/   diagnostics. Flash this when hardware misbehaves.
```

Both need a `secrets.h` beside the `.ino` — copy `secrets.h.example` and fill
it in. That file is gitignored; nothing here should ever carry a password to
GitHub.

---

## The two-core architecture, and why it matters

```
core 1 (Arduino loop)   LCD, LED, card reader.  Never blocks.
core 0 (netTask)        WiFi and every HTTP call.  May block freely.
```

This is not decoration. On ESP32, `WiFiClient::connect(ip, port, timeout)` and
`HTTPClient::setConnectTimeout()` are **not reliably honoured**
(arduino-esp32 [#5168](https://github.com/espressif/arduino-esp32/issues/5168),
[#7057](https://github.com/espressif/arduino-esp32/issues/7057)). A call that
should give up after 1.5 s can block for thirty seconds instead.

That is the exact failure that cost two days: the screen froze on `Connected`
and looked crashed while the sketch sat inside an HTTP call whose timeout was
being ignored. Setting timeouts cannot fix it, because setting timeouts is the
thing that doesn't work.

Putting the network on the other core makes the bug harmless. If a call hangs,
the clock still ticks, the lamp still blinks and the reader still reads. The
two tasks share no peripherals — SPI and I²C only from core 1, WiFi only from
core 0 — so the only shared thing is one small mailbox behind a mutex.

---

## The five bugs this firmware is written around

| # | Bug | What it does |
|---|---|---|
| 1 | **TX power** | Cheap N16R8 boards can't complete a WPA2 handshake at 19.5 dBm — the frames arrive corrupted and you get `reason 2, AUTH_EXPIRE` on *every* network. `connectWiFi()` walks a ladder 19.5 → 11 → 8.5 → 5 dBm and keeps what works. |
| 2 | **Timeouts ignored** | See above — the network lives on core 0. |
| 3 | **The reader dies** | An MFRC522 stops answering after a few hours ([#540](https://github.com/miguelbalboa/rfid/issues/540)). `rfidWatchdog()` reads its version register every 5 s and re-initialises it if it has gone quiet. |
| 4 | **I²C can lock up** | `Wire` can block forever on a glitching device ([#349](https://github.com/espressif/arduino-esp32/issues/349)). `Wire.setTimeOut(50)` bounds it. |
| 5 | **Heap fragmentation** | `String` churn kills long-running ESP32 sketches. The network path uses fixed `char` buffers; free heap is logged every 30 s. |

Plus every `millis()` comparison is written `(now - then >= interval)` — the
unsigned form that survives the 49-day rollover.

---

## Three rules in the live loop

- **Nothing blocks.** The LED blink is a state machine, never `delay()`. Three
  blinks at 200 ms would leave the reader deaf and the screen frozen for 1.2 s.
- **Same card ignored for 3 seconds.** The RC522 reports a card continuously
  while it sits in the field; unguarded, one tap becomes start/end/start about
  twenty times a second.
- **Taps answer, polls stay quiet.** A failed poll changes nothing on screen —
  a dropped packet on a MiFi is normal and the next poll is a second away. A
  failed tap says `No server / Try again`, because a teacher who taps and sees
  nothing taps harder and then assumes the device is broken.

**The screen never sits on a message that has stopped being true.** No WiFi →
it says so with the reason code. Server unreachable → it says so after five
tries. There is no "connecting…" screen anywhere, because a screen that sits on
one message is what made a three-second delay look like a crash.

---

## The idea worth knowing

**The screen is rendered on the server.** Every endpoint returns four strings
already padded to the device's column count, plus what the lamp should do.

```
tap happened?   POST /api/device/tap     -> print what comes back
on a timer      GET  /api/device/poll    -> print what comes back
showed a flag?  POST /api/device/shown
```

Everything that can be *wrong* lives in `app/routers/device.py`: session
timing, which flag to show, how to abbreviate a name into 20 characters, when
to suppress a repeat. Changing the flag cooldown from 90 s to 60, or the
wording on a screen, is a Python edit and a server restart — never a reflash.
That matters most the night before a competition.

The card appears in exactly one function, `readCard()`. Set `USE_RFID 0` and
the BOOT button becomes the card; nothing else in the sketch changes.

---

## Testing a flag by hand

The flags router is mounted at **`/flags`**, not `/api/flags`. With a lesson
open, `POST /flags`:

```json
{ "student_external_id": "stu-01",
  "flag_type": "gaze_away",
  "confidence": 0.95 }
```

**Two traps, both of which look exactly like a device fault:**

1. **Confidence below 0.5** is stored with `status='dismissed'` and never
   reaches the device. The response tells you: `auto_dismissed: true`.
2. **The student must be in the device's class.** Device 1 is bound to class 1
   (P5 Mathematics), so `stu-01` (Beshoy) and `stu-06` (Lo2lo2) appear;
   `stu-02` and `stu-03` are silently ignored.

---

## Diagnostics — `souly_selftest/`

Seven checks in dependency order. It does **not** stop at the first failure —
a failed check marks its dependents skipped and the run continues, so one
upload reports everything that is wrong.

| # | Passes when | A failure means |
|---|---|---|
| 1 `LCD` | something answers on I²C | SDA→4, SCL→5, VCC→5V |
| 2 `LED` | you see three slow blinks | GPIO 1, resistor, polarity |
| 3 `RFID` | version register reads `0x91`/`0x92` | `0x00`/`0xFF` = not on the bus |
| 4 `CARD` | a UID within 30 s (BOOT skips) | warns, does not fail |
| 5 `WIFI` | associated at some TX power | prints the reason code *and* which power worked |
| 6 `TCP` | raw socket connects under 3 s | **timed out** = firewall dropping; **refused** = uvicorn down |
| 7 `API` | `POST /hello` 200 and parses | 401 = wrong `DEVICE_KEY` |

```
1 LCD  OK  5 WIFI OK
2 LED  OK  6 TCP  OK
3 RFID OK  7 API  OK
4 CARD OK  ALL PASS
```

`OK` pass · `--` fail · `??` warn · `sk` skipped.

If the screen and serial are both dead, the LED blinks the **number of the
first failed check** — which is why check 1 is not allowed to be the only thing
that reports.

WiFi reason codes: `2` auth expired (**on this board, TX power**) · `15` wrong
password · `201` no AP / 5GHz only · `202` auth failed · `203` assoc refused ·
`204` handshake timeout.

A firewall that **refuses** answers instantly; one that **drops** makes you
wait the full timeout. The elapsed time is the diagnosis.

---

## Wiring

| RC522 | ESP32-S3 | | LCD | ESP32-S3 |
|---|---|---|---|---|
| SDA (SS) | GPIO 10 | | SDA | GPIO 4 |
| SCK | GPIO 12 | | SCL | GPIO 5 |
| MOSI | GPIO 11 | | VCC | **5V** |
| MISO | GPIO 13 | | GND | GND |
| RST | GPIO 9 | | | |
| IRQ | *unconnected* | | **LED** | GPIO 1 + 220–330 Ω → GND |
| VCC | **3.3V — never 5V** | | | |

**Three things learned the hard way:**

- It is an **R8 part with 8 MB octal PSRAM**, so GPIO 33–37 belong to the PSRAM
  and cannot be used. MOSI and MISO were originally on 35 and 37 and could
  never have worked.
- The LCD is a **2004A: 20 columns, not 16**. Its blue trimmer is *contrast*;
  backlight brightness is the jumper beside it.
- The RC522 must be on **3.3 V**. On 5 V its power LED still lights and 3.3 V
  still measures fine at the pins, but the digital side is dead — which looks
  exactly like a wiring fault and isn't one.

---

## Arduino IDE

**Board:** ESP32S3 Dev Module

| Setting | Value | Why |
|---|---|---|
| USB CDC On Boot | **Enabled** | Otherwise `Serial` goes to UART0 on GPIO 43/44 and the monitor stays blank forever |
| USB Mode | Hardware CDC and JTAG | |
| Erase All Flash | Enable once after changing partition scheme | A stale partition table boot-loops the board |

**Libraries:** `LiquidCrystal_I2C` (Frank de Brabander), `MFRC522`
(GithubCommunity 1.4.x — **not** MFRC522v2), `ArduinoJson` **7.x**.

Build size: ~753 KB flash (57%), 47 KB RAM.

Upload failing with *"No serial data received"*: hold **BOOT**, tap **RESET**,
release **BOOT**, then upload. The COM port often changes when the board enters
bootloader mode, so re-pick it in Tools → Port first.

---

## Enrolling a card

MIFARE UIDs are burned in at the factory — there is nothing to program. Check 4
of the self-test prints the UID of whatever you tap:

```
python scripts/seed_classes.py --card sarah=A3F21C08
```

A UID is **identification, not authentication** — it is broadcast in the clear
and clones for the price of a coffee. It says which teacher is at the door. The
teacher's real credential is the password on their account.
