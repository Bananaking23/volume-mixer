# ESP32-C3 Audio Mixer Configurator

A desktop app (Python + Tkinter, standard library only) that lets you configure a
five-slider, two-button ESP32-C3 SuperMini deej mixer with a 0.96" monochrome OLED
**without hand-editing Arduino code**. It generates an Arduino `.ino` sketch from your
settings, opens it in the Arduino IDE, and can optionally compile/upload it with
Arduino CLI.

**Hardware:** ESP32-C3 SuperMini, 5 x 10k potentiometers, 2 push buttons,
128x64 I2C OLED (SSD1306-compatible), and optionally a WS2812/NeoPixel LED strip.

## 1. Launch the app

Requires Python 3.9+ (with Tkinter, included in the standard Windows installer).

```
python desktop_app.py
```

No `pip install` needed. Settings are stored in `mixer_config.json` next to the app.
Older config files that lack newer fields are migrated automatically (missing values
fall back to defaults); a corrupt file falls back to defaults instead of crashing.

## 2. One-time Arduino setup

### Install Arduino CLI
Download the Windows build from <https://arduino.github.io/arduino-cli/> and either:
- put `arduino-cli.exe` on your `PATH`, **or**
- keep it anywhere and use **Browse...** in the app to select it.

### Install the ESP32 core
```
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32
```

### Install U8g2
```
arduino-cli lib install U8g2
```
(In Arduino IDE: *Sketch > Include Library > Manage Libraries...* and search "U8g2".)

If **Enable LED strip** is selected in the Hardware tab, also install the
Adafruit NeoPixel library:

```
arduino-cli lib install "Adafruit NeoPixel"
```

`Wire` and `Preferences` ship with the ESP32 core; nothing else is required.

## 3. Using the app

Connect the ESP32-C3 over USB, then click **Quick Setup**. It will:
1. look for Arduino CLI (PATH, then common install folders, then ask you to Browse),
2. detect COM ports via Arduino CLI **and** fall back to the Windows `SERIALCOMM`
   registry key if the CLI isn't available,
3. fill the COM port dropdown.

Tabs:

| Tab | What it does |
|---|---|
| **Hardware** | Slider/button GPIOs, OLED SDA/SCL/address/rotation, optional WS2812/NeoPixel strip settings, board FQBN, baud rate, hardware profiles. Inline warnings for duplicate GPIOs, non-ADC slider pins, boot/USB pins, invalid GPIOs, bad OLED address, missing FQBN. |
| **Channels & Actions** | Per-channel name, OLED label, invert, ADC min/max, visibility, display style; per-button action and trigger (press/release). |
| **OLED Designer** | Title/footer, layouts (Meters, Numeric, Status, Minimal), vertical/horizontal bars, widget layouts, master/mute/peak/numeric options, with a live preview. |
| **Build & Upload** | Arduino CLI path, COM port, **Open Sketch**, **Generate & Upload**, **Upload Now**, live compile/upload log. |

Default pins: sliders GPIO0-4, buttons GPIO10 and GPIO20, OLED SDA/SCL GPIO8/9 (or 6/7
via the second hardware profile). The optional LED strip defaults to GPIO5 and is
disabled until enabled. Its modes are **Per-channel levels**, **Master level**,
**Solid color**, and **Rainbow cycle**. Level modes blend from the editable Low
color to High color; Solid color uses High color.
Note GPIO2 (slider 3) is a boot strapping pin, so
the app shows a warning for the default layout; it works on most boards but is worth
knowing about if the board misbehaves at boot.

### The OLED is monochrome
The physical display is black and white only. The preview draws strictly white pixels
on black. The **Appearance** dialog only changes desktop chrome (accent color and the
preview bezel); accent colors are never shown as OLED colors.

### Open the generated sketch
**Open Sketch** saves the config, generates
`arduino_build/deej_esp32/deej_esp32.ino`, and opens it with the Windows file
association (normally the Arduino IDE).

### Test the LED strip by itself
Use the **Hardware Tests** tab to generate or open
`arduino_build/led_strip_test/led_strip_test.ino`. This standalone sketch tests
only the LED strip. Choose **Full sequence**, **Red**, **Green**, **Blue**, **Low
color**, **High color**, **White chase**, or **Rainbow cycle** before generating
the sketch. The same tab can generate isolated **Potentiometers**, **Buttons**,
or **OLED display** tests. Each selected test omits the other hardware and the
mixer serial protocol.

> The sketch lives in a folder with the same name as the `.ino`
> (`deej_esp32/deej_esp32.ino`) because both Arduino IDE and Arduino CLI require
> that. This is a small deliberate change from a flat `arduino_build\deej_esp32.ino`.

### Compile and upload from Arduino IDE
1. Click **Open Sketch**.
2. *Tools > Board > esp32 > ESP32C3 Dev Module* (FQBN `esp32:esp32:esp32c3`).
3. Enable *Tools > USB CDC On Boot* if you want serial over the native USB port.
4. Choose your COM port and click **Upload**.

### Compile and upload from the app (Arduino CLI)
Set the CLI path and COM port, then **Generate & Upload** (regenerates, compiles,
uploads) or **Upload Now** (uses the existing sketch). Output streams live and
exit codes are reported. Upload is blocked until the COM port, CLI path and FQBN
are valid and the config has no errors.

The **Build & Upload** tab is intentionally focused on uploading: choose the
Arduino CLI, refresh/select the COM port, choose a target (**Main mixer**, **LED
strip test**, **Potentiometers test**, **Buttons test**, or **OLED display test**),
then use **Generate & Upload** or **Upload Existing**. Detailed CLI output
appears below the upload controls.

## 4. deej serial output

The firmware prints one line per update (default about every 30 ms):

```
0|512|1023|300|900
```

Five values, each 0-1023, pipe separated, in slider order, at your chosen baud rate
(default 115200). Point deej at the ESP32's COM port with matching baud and a
`slider_mapping` for 5 sliders. Values respect per-channel invert, ADC calibration,
smoothing and mute state.

## 5. Button shortcuts

Each button has an action and a trigger (**On press** / **On release**):

`None`, `Recalibrate`, `MuteAll`, `MuteNext`, `MutePrevious`, `ScreenOn`, `ScreenOff`,
`ToggleScreen`, `F13`-`F24`, or `Custom shortcut`.

- `MuteAll` toggles all channels to 0 in the deej output; `MuteNext`/`MutePrevious`
  move a cursor and toggle that channel's mute.
- `Recalibrate` samples for about 4 seconds: sweep every slider fully during that
  time. New min/max are saved on the board (ESP32 NVS) and reused after reboot.
- Page actions are intentionally not included (no paged UI yet).

F-keys and custom shortcuts (`CTRL+ALT+M`, `SHIFT+F13`, `MEDIA_PLAY_PAUSE`, ...) are
sent as tagged serial lines:

```
@shortcut:F13
@shortcut:CTRL+ALT+M
```

> **These lines require a desktop shortcut bridge.** The ESP32 firmware is a serial
> device only; it does **not** act as a USB keyboard. A small program on the PC must
> read the COM port, watch for `@shortcut:` lines, and send real key events. Note deej
> itself also reads this port, so the bridge and deej need to share the stream (or the
> bridge must forward the volume lines).

## 6. Tests

```
python tests/test_all.py
```

Checks Python syntax, default and custom firmware generation, balanced braces,
config migration (old, corrupt, missing files) and, when `g++` is installed,
syntax-checks each generated sketch against stub Arduino/U8g2 headers in
`tests/arduino_stubs/`.

**Not covered:** the stub check does not replace a real compile against the ESP32
core and U8g2, and nothing here was run on hardware. Hardware validation is pending:
build and upload with the board connected, then confirm the serial output and OLED.
The Tkinter UI was syntax-checked but not launched in the build environment (no display).

## Files

| File | Purpose |
|---|---|
| `desktop_app.py` | Tkinter UI |
| `config.py` | Defaults, load/save, migration, validation |
| `firmware_generator.py` | Generates the `.ino` |
| `arduino_tools.py` | Arduino CLI, COM ports, compile/upload |
| `theme.py` | Dark theme |
| `tests/` | Regression tests and stub headers |
