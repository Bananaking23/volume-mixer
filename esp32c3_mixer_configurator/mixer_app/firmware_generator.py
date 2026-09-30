"""Generates a self-contained Arduino .ino sketch for the ESP32-C3 audio
mixer from a validated config dict (see config.py).

The generated firmware:
  * Reads 5 analog sliders at 12-bit resolution with exponential smoothing
  * Emits deej-style serial lines: "0|512|1023|300|900\\n"
  * Supports per-channel invert + min/max calibration
  * Debounces 2 push buttons and dispatches configurable actions
  * Persists slider calibration to NVS (Preferences) via the Recalibrate action
  * Drives a monochrome SSD1306 OLED through U8g2 with a configurable layout
  * Optionally drives a WS2812/NeoPixel LED strip as a level meter
  * Never stores secrets/credentials of any kind
"""
from __future__ import annotations

import datetime

from config import OLED_ROTATIONS

CALIBRATION_MS = 4000  # how long the "Recalibrate" action samples for


def _c_str(s: str) -> str:
    """Escape a Python string for embedding as a C string literal."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _style_enum(style: str) -> str:
    return {"Bar": 0, "Number": 1, "Hidden": 2}.get(style, 0)


def generate_firmware(cfg: dict) -> str:
    hw = cfg["hardware"]
    channels = cfg["channels"]
    button_actions = cfg["button_actions"]
    oled = cfg["oled_designer"]
    fw = cfg["firmware"]

    n_ch = len(channels)
    n_btn = len(button_actions)

    slider_pins = hw["slider_pins"]
    button_pins = hw["button_pins"]
    sda, scl = hw["oled_sda"], hw["oled_scl"]
    addr = int(str(hw["oled_address"]), 16)
    rotation_const = OLED_ROTATIONS.get(hw.get("oled_rotation", "0 degrees"), "U8G2_R0")
    baud = hw["baud_rate"]
    led_strip = hw.get("led_strip", {})
    led_enabled = bool(led_strip.get("enabled", False))
    led_pin = int(led_strip.get("data_pin", 5))
    led_count = int(led_strip.get("led_count", 5))
    led_brightness = int(led_strip.get("brightness", 64))
    led_mode = led_strip.get("mode", "Per-channel levels")

    def color_channels(value, default):
      text = str(value or default).lstrip("#")
      try:
        number = int(text, 16)
        return (number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF
      except ValueError:
        return color_channels(default, default) if text != default.lstrip("#") else (0, 255, 0)

    low_r, low_g, low_b = color_channels(led_strip.get("low_color"), "#00FF00")
    high_r, high_g, high_b = color_channels(led_strip.get("high_color"), "#FF0000")

    smoothing = float(fw.get("smoothing", 0.2))
    interval_ms = int(fw.get("update_interval_ms", 30))

    adc_min = [int(c["adc_min"]) for c in channels]
    adc_max = [int(c["adc_max"]) for c in channels]
    invert = [bool(c["invert"]) for c in channels]
    visible = [bool(c["visible"]) for c in channels]
    style = [_style_enum(c["style"]) for c in channels]
    labels = [c["label"] for c in channels]

    widget_layout = oled.get("widget_layout", "Five channels")
    layout = oled.get("layout", "Meters")
    meter_style = oled.get("meter_style", "Vertical")

    # ------------------------------------------------------------------
    # Small helpers to build C array literals
    # ------------------------------------------------------------------
    def int_array(values):
        return "{" + ", ".join(str(v) for v in values) + "}"

    def bool_array(values):
        return "{" + ", ".join(_bool(v) for v in values) + "}"

    def str_array(values):
        return "{" + ", ".join(_c_str(v) for v in values) + "}"

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Button action dispatch (inlined per button since actions are fixed
    # at generation time -- this avoids any runtime lookup complexity and
    # always compiles cleanly).
    # ------------------------------------------------------------------
    def action_body(action: str, custom: str, btn_idx: int) -> str:
        if action == "None":
            return "    // no action configured"
        if action == "Recalibrate":
            return "    startCalibration();"
        if action == "MuteAll":
            return "    allMuted = !allMuted;"
        if action == "MuteNext":
            return (
                "    muteCursor = (muteCursor + 1) % NUM_CHANNELS;\n"
                "    channelMuted[muteCursor] = !channelMuted[muteCursor];"
            )
        if action == "MutePrevious":
            return (
                "    muteCursor = (muteCursor - 1 + NUM_CHANNELS) % NUM_CHANNELS;\n"
                "    channelMuted[muteCursor] = !channelMuted[muteCursor];"
            )
        if action == "ScreenOn":
            return "    screenOn = true;\n    u8g2.setPowerSave(0);"
        if action == "ScreenOff":
            return "    screenOn = false;\n    u8g2.setPowerSave(1);"
        if action == "ToggleScreen":
            return "    screenOn = !screenOn;\n    u8g2.setPowerSave(screenOn ? 0 : 1);"
        if action.startswith("F") and action[1:].isdigit():
            return f'    Serial.print(F("@shortcut:{action}\\n"));'
        if action == "Custom shortcut":
            escaped = custom.replace("\\", "\\\\").replace('"', '\\"')
            return f'    Serial.print(F("@shortcut:{escaped}\\n"));'
        return "    // unknown action"

    button_case_blocks = []
    for i, ba in enumerate(button_actions):
        body = action_body(ba["action"], ba.get("custom", ""), i)
        button_case_blocks.append(f"  if (idx == {i}) {{\n{body}\n    return;\n  }}")
    button_dispatch = "\n".join(button_case_blocks) if button_case_blocks else "  // no buttons configured"

    button_trigger_on_press = int_array(
        [1 if ba["trigger"] == "On press" else 0 for ba in button_actions]
    )

    # ------------------------------------------------------------------
    # OLED drawing routines, selected by layout
    # ------------------------------------------------------------------
    draw_functions = _build_draw_functions(oled, n_ch)
    led_include = "#include <Adafruit_NeoPixel.h>\n" if led_enabled else ""
    led_defines = f"""\
  #define LED_STRIP_ENABLED {str(led_enabled).lower()}
  #define LED_DATA_PIN {led_pin}
  #define LED_COUNT {led_count}
  #define LED_BRIGHTNESS {led_brightness}
  #define LED_LOW_R {low_r}
  #define LED_LOW_G {low_g}
  #define LED_LOW_B {low_b}
  #define LED_HIGH_R {high_r}
  #define LED_HIGH_G {high_g}
  #define LED_HIGH_B {high_b}
  """
    led_object = (
      "Adafruit_NeoPixel ledStrip(LED_COUNT, LED_DATA_PIN, NEO_GRB + NEO_KHZ800);"
      if led_enabled else ""
    )
    if led_enabled:
      led_update = _build_led_update(led_mode)
      led_setup = "  ledStrip.begin();\n  ledStrip.setBrightness(LED_BRIGHTNESS);\n  ledStrip.show();"
      led_loop = "    updateLedStrip();"
    else:
      led_update = ""
      led_setup = ""
      led_loop = ""

    sketch = f"""\
/*
 * Auto-generated by the ESP32-C3 Audio Mixer Configurator.
 * Generated: {timestamp}
 * DO NOT hand-edit this file -- regenerate it from the desktop app instead.
 *
 * Hardware: ESP32-C3 SuperMini, {n_ch} analog slider(s), {n_btn} push button(s),
 * 128x64 monochrome I2C OLED (SSD1306-compatible) driven via U8g2.
 * LED strip: {"WS2812/NeoPixel enabled" if led_enabled else "disabled"}.
 *
 * Serial protocol: one deej-compatible line per update, e.g.
 *   0|512|1023|300|900
 * Button shortcut actions are emitted as tagged lines, e.g.
 *   @shortcut:F13
 *   @shortcut:CTRL+ALT+M
 * These tagged lines require a desktop shortcut bridge to become real
 * OS keyboard events -- this firmware is a serial device only, it does
 * NOT act as a USB HID keyboard.
 *
 * No secrets or external credentials are stored on this device.
 */

#include <Wire.h>
#include <U8g2lib.h>
#include <Preferences.h>
{led_include}

// ---------------------------------------------------------------------
// Configuration (generated)
// ---------------------------------------------------------------------
#define NUM_CHANNELS {n_ch}
#define NUM_BUTTONS  {n_btn}

#define OLED_SDA_PIN {sda}
#define OLED_SCL_PIN {scl}
#define OLED_I2C_ADDR 0x{addr:02X}
{led_defines}

#define SERIAL_BAUD {baud}
#define UPDATE_INTERVAL_MS {interval_ms}
#define DEBOUNCE_MS 30
#define CALIBRATION_MS {CALIBRATION_MS}

const int SLIDER_PINS[NUM_CHANNELS] = {int_array(slider_pins)};
const int BUTTON_PINS[NUM_BUTTONS]  = {int_array(button_pins) if n_btn else "{}"};
const int BUTTON_TRIGGER_ON_PRESS[NUM_BUTTONS] = {button_trigger_on_press if n_btn else "{}"}; // 1 = fire on press, 0 = fire on release

int ADC_MIN[NUM_CHANNELS] = {int_array(adc_min)};
int ADC_MAX[NUM_CHANNELS] = {int_array(adc_max)};
const bool CH_INVERT[NUM_CHANNELS]  = {bool_array(invert)};
const bool CH_VISIBLE[NUM_CHANNELS] = {bool_array(visible)};
const int  CH_STYLE[NUM_CHANNELS]   = {int_array(style)}; // 0=Bar 1=Number 2=Hidden
const char* CH_LABEL[NUM_CHANNELS]  = {str_array(labels)};

const float SMOOTHING_ALPHA = {smoothing}f; // weight kept from the previous sample (0=none .. <1=heavy)
int channelOutput[NUM_CHANNELS];

const char* OLED_TITLE  = {_c_str(oled.get("title", ""))};
const char* OLED_FOOTER = {_c_str(oled.get("footer", ""))};
const bool SHOW_TITLE   = {_bool(oled.get("show_title", True))};
const bool SHOW_FOOTER  = {_bool(oled.get("show_footer", True))};
const bool SHOW_STATUS  = {_bool(oled.get("show_status", True))};
const bool SHOW_LABELS  = {_bool(oled.get("show_labels", True))};
const bool SHOW_MASTER  = {_bool(oled.get("show_master", False))};
const bool SHOW_MUTE_INDICATOR = {_bool(oled.get("show_mute_indicator", True))};
const bool SHOW_PEAK_INDICATOR = {_bool(oled.get("show_peak_indicator", False))};
const bool SHOW_NUMERIC_VALUES = {_bool(oled.get("show_numeric_values", True))};

// ---------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2({rotation_const}, /* reset=*/ U8X8_PIN_NONE);
Preferences prefs;
{led_object}

float smoothed[NUM_CHANNELS];
bool channelMuted[NUM_CHANNELS];
bool allMuted = false;
int muteCursor = 0;
bool screenOn = true;

bool calibrating = false;
unsigned long calibrationEndAt = 0;
int calMin[NUM_CHANNELS];
int calMax[NUM_CHANNELS];

int lastRawState[NUM_BUTTONS];
int stableState[NUM_BUTTONS];
unsigned long lastEdgeAt[NUM_BUTTONS];

unsigned long lastUpdateAt = 0;

// ---------------------------------------------------------------------
// Calibration persistence (NVS via Preferences, no external credentials)
// ---------------------------------------------------------------------
void loadCalibration() {{
  prefs.begin("mixer", true);
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    char keyMin[8]; char keyMax[8];
    snprintf(keyMin, sizeof(keyMin), "min%d", i);
    snprintf(keyMax, sizeof(keyMax), "max%d", i);
    if (prefs.isKey(keyMin)) ADC_MIN[i] = prefs.getInt(keyMin, ADC_MIN[i]);
    if (prefs.isKey(keyMax)) ADC_MAX[i] = prefs.getInt(keyMax, ADC_MAX[i]);
  }}
  prefs.end();
}}

void saveCalibration() {{
  prefs.begin("mixer", false);
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    char keyMin[8]; char keyMax[8];
    snprintf(keyMin, sizeof(keyMin), "min%d", i);
    snprintf(keyMax, sizeof(keyMax), "max%d", i);
    prefs.putInt(keyMin, ADC_MIN[i]);
    prefs.putInt(keyMax, ADC_MAX[i]);
  }}
  prefs.end();
}}

void startCalibration() {{
  calibrating = true;
  calibrationEndAt = millis() + CALIBRATION_MS;
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    calMin[i] = 4095;
    calMax[i] = 0;
  }}
}}

void updateCalibration(int idx, int raw) {{
  if (raw < calMin[idx]) calMin[idx] = raw;
  if (raw > calMax[idx]) calMax[idx] = raw;
}}

void finishCalibrationIfDue() {{
  if (!calibrating) return;
  if ((long)(millis() - calibrationEndAt) < 0) return;
  calibrating = false;
  bool changed = false;
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    if (calMax[i] > calMin[i] + 32) {{ // ignore noise-only sweeps
      ADC_MIN[i] = calMin[i];
      ADC_MAX[i] = calMax[i];
      changed = true;
    }}
  }}
  if (changed) saveCalibration();
}}

// ---------------------------------------------------------------------
// Button actions
// ---------------------------------------------------------------------
void handleButtonAction(int idx) {{
{button_dispatch}
}}

void pollButtons() {{
  unsigned long now = millis();
  for (int i = 0; i < NUM_BUTTONS; i++) {{
    int raw = digitalRead(BUTTON_PINS[i]); // INPUT_PULLUP: LOW = pressed

    if (raw != lastRawState[i]) {{
      lastEdgeAt[i] = now;
      lastRawState[i] = raw;
    }}

    if ((now - lastEdgeAt[i]) > DEBOUNCE_MS && raw != stableState[i]) {{
      stableState[i] = raw;
      bool pressed = (stableState[i] == LOW);
      bool fireOnPress = (BUTTON_TRIGGER_ON_PRESS[i] == 1);
      if ((pressed && fireOnPress) || (!pressed && !fireOnPress)) {{
        handleButtonAction(i);
      }}
    }}
  }}
}}

// ---------------------------------------------------------------------
// Sliders / serial output
// ---------------------------------------------------------------------
int mapChannel(int idx, float rawSmoothed) {{
  long v = map((long)rawSmoothed, ADC_MIN[idx], ADC_MAX[idx], 0, 1023);
  v = constrain(v, 0L, 1023L);
  if (CH_INVERT[idx]) v = 1023 - v;
  if (allMuted || channelMuted[idx]) v = 0;
  return (int)v;
}}

void pollSlidersAndReport() {{
  String line = "";
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    int raw = analogRead(SLIDER_PINS[i]); // 12-bit: 0-4095
    smoothed[i] = (smoothed[i] * SMOOTHING_ALPHA) + ((float)raw * (1.0f - SMOOTHING_ALPHA));

    if (calibrating) updateCalibration(i, raw);

    int outVal = mapChannel(i, smoothed[i]);
    channelOutput[i] = outVal;
    line += String(outVal);
    if (i < NUM_CHANNELS - 1) line += "|";
  }}
  Serial.println(line);
{led_loop}
}}

{led_update}
// ---------------------------------------------------------------------
// OLED rendering
// ---------------------------------------------------------------------
{draw_functions}

void drawScreen() {{
  u8g2.clearBuffer();
  drawLayout();
  u8g2.sendBuffer();
}}

// ---------------------------------------------------------------------
// Arduino entry points
// ---------------------------------------------------------------------
void setup() {{
  Serial.begin(SERIAL_BAUD);

  for (int i = 0; i < NUM_CHANNELS; i++) {{
    smoothed[i] = (float)((ADC_MIN[i] + ADC_MAX[i]) / 2);
    channelMuted[i] = false;
  }}

  for (int i = 0; i < NUM_BUTTONS; i++) {{
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
    lastRawState[i] = digitalRead(BUTTON_PINS[i]);
    stableState[i] = lastRawState[i];
    lastEdgeAt[i] = millis();
  }}

  analogReadResolution(12);

  loadCalibration();

  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  u8g2.begin();
  u8g2.setI2CAddress(OLED_I2C_ADDR << 1);
  u8g2.setPowerSave(0);
{led_setup}
}}

void loop() {{
  unsigned long now = millis();

  pollButtons();
  finishCalibrationIfDue();

  if (now - lastUpdateAt >= (unsigned long)UPDATE_INTERVAL_MS) {{
    lastUpdateAt = now;
    pollSlidersAndReport();
    if (screenOn) drawScreen();
  }}
}}
"""
    return sketch


def _build_led_update(mode: str) -> str:
    if mode == "Solid color":
        level_source = """  for (int i = 0; i < LED_COUNT; i++) {
    ledStrip.setPixelColor(i, ledStrip.Color(LED_HIGH_R, LED_HIGH_G, LED_HIGH_B));
  }"""
    elif mode == "Rainbow cycle":
        level_source = """  for (int i = 0; i < LED_COUNT; i++) {
    uint8_t position = (uint8_t)((i * 256 / LED_COUNT + millis() / 8) & 255);
    ledStrip.setPixelColor(i, colorWheel(position));
  }"""
    elif mode == "Master level":
        level_source = """  int total = 0;
  for (int i = 0; i < NUM_CHANNELS; i++) total += channelOutput[i];
  int level = total / NUM_CHANNELS;
  for (int i = 0; i < LED_COUNT; i++) {
    setLedLevel(i, level);
  }"""
    else:
        level_source = """  for (int i = 0; i < LED_COUNT; i++) {
    int channel = (i * NUM_CHANNELS) / LED_COUNT;
    setLedLevel(i, channelOutput[channel]);
  }"""
    wheel_function = """\
uint32_t colorWheel(uint8_t position) {
  position = 255 - position;
  if (position < 85) return ledStrip.Color(255 - position * 3, 0, position * 3);
  if (position < 170) {
    position -= 85;
    return ledStrip.Color(0, position * 3, 255 - position * 3);
  }
  position -= 170;
  return ledStrip.Color(position * 3, 255 - position * 3, 0);
}

""" if mode == "Rainbow cycle" else ""
    return f"""\
{wheel_function}void setLedLevel(int index, int level) {{
  level = constrain(level, 0, 1023);
  uint8_t red = (uint8_t)(LED_LOW_R + (LED_HIGH_R - LED_LOW_R) * level / 1023);
  uint8_t green = (uint8_t)(LED_LOW_G + (LED_HIGH_G - LED_LOW_G) * level / 1023);
  uint8_t blue = (uint8_t)(LED_LOW_B + (LED_HIGH_B - LED_LOW_B) * level / 1023);
  ledStrip.setPixelColor(index, ledStrip.Color(red, green, blue));
}}

void updateLedStrip() {{
{level_source}
  ledStrip.show();
}}
"""


def generate_led_test_firmware(cfg: dict, test_name: str = "Full sequence") -> str:
    """Generate a standalone LED-only diagnostic sketch."""
    led = cfg["hardware"].get("led_strip", {})
    pin = int(led.get("data_pin", 5))
    count = int(led.get("led_count", 5))
    brightness = int(led.get("brightness", 64))

    def color(value, default):
        try:
            number = int(str(value or default).lstrip("#"), 16)
        except ValueError:
            number = int(default.lstrip("#"), 16)
        return (number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF

    low_r, low_g, low_b = color(led.get("low_color"), "#00FF00")
    high_r, high_g, high_b = color(led.get("high_color"), "#FF0000")
    extra = ""
    test_steps = {
        "Red": "  showColor(255, 0, 0);",
        "Green": "  showColor(0, 255, 0);",
        "Blue": "  showColor(0, 0, 255);",
        "Low color": f"  showColor({low_r}, {low_g}, {low_b});",
        "High color": f"  showColor({high_r}, {high_g}, {high_b});",
        "White chase": "  chaseColor(255, 255, 255);",
    }
    if test_name == "Rainbow cycle":
        extra = """\
uint32_t wheel(uint8_t position) {
  position = 255 - position;
  if (position < 85) return strip.Color(255 - position * 3, 0, position * 3);
  if (position < 170) {
    position -= 85;
    return strip.Color(0, position * 3, 255 - position * 3);
  }
  position -= 170;
  return strip.Color(position * 3, 255 - position * 3, 0);
}

void showRainbow() {
  for (int offset = 0; offset < 256; offset++) {
    for (int i = 0; i < LED_COUNT; i++) {
      strip.setPixelColor(i, wheel((i * 256 / LED_COUNT + offset) & 255));
    }
    strip.show();
    delay(10);
  }
}

"""
    loop_body = "  showRainbow();" if test_name == "Rainbow cycle" else test_steps.get(
        test_name,
        "  showColor(255, 0, 0);\n  showColor(0, 255, 0);\n  showColor(0, 0, 255);\n"
        f"  showColor({low_r}, {low_g}, {low_b});\n"
        f"  showColor({high_r}, {high_g}, {high_b});\n"
        "  chaseColor(255, 255, 255);",
    )
    return f"""\
/*
 * Standalone LED strip diagnostic sketch.
 * This tests only the WS2812/NeoPixel strip: no OLED, pots, buttons, or mixer code.
 */
#include <Adafruit_NeoPixel.h>

#define LED_DATA_PIN {pin}
#define LED_COUNT {count}
#define LED_BRIGHTNESS {brightness}

Adafruit_NeoPixel strip(LED_COUNT, LED_DATA_PIN, NEO_GRB + NEO_KHZ800);

{extra if test_name == "Rainbow cycle" else ""}
void showColor(uint8_t red, uint8_t green, uint8_t blue) {{
  for (int i = 0; i < LED_COUNT; i++) strip.setPixelColor(i, strip.Color(red, green, blue));
  strip.show();
  delay(700);
}}

void chaseColor(uint8_t red, uint8_t green, uint8_t blue) {{
  for (int i = 0; i < LED_COUNT; i++) {{
    strip.clear();
    strip.setPixelColor(i, strip.Color(red, green, blue));
    strip.show();
    delay(120);
  }}
}}

void setup() {{
  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  strip.clear();
  strip.show();
}}

void loop() {{
{loop_body}
}}
"""


def generate_hardware_test_firmware(cfg: dict, test_name: str) -> str:
    """Generate a standalone diagnostic sketch for one non-LED subsystem."""
    hw = cfg["hardware"]
    if test_name == "Potentiometers":
        pins = ", ".join(str(pin) for pin in hw["slider_pins"])
        return f"""\
/* Standalone potentiometer test. Values are printed as 0-4095 ADC readings. */
#define NUM_POTS {len(hw["slider_pins"])}
const int POT_PINS[NUM_POTS] = {{{pins}}};

void setup() {{
  Serial.begin({int(hw["baud_rate"])});
  analogReadResolution(12);
}}

void loop() {{
  for (int i = 0; i < NUM_POTS; i++) {{
    if (i) Serial.print('|');
    Serial.print(analogRead(POT_PINS[i]));
  }}
  Serial.println();
  delay(100);
}}
"""

    if test_name == "Buttons":
        pins = ", ".join(str(pin) for pin in hw["button_pins"])
        return f"""\
/* Standalone button test. Pressed buttons print BUTTON n PRESSED. */
#define NUM_BUTTONS {len(hw["button_pins"])}
const int BUTTON_PINS[NUM_BUTTONS] = {{{pins}}};
int previous[NUM_BUTTONS];

void setup() {{
  Serial.begin({int(hw["baud_rate"])});
  for (int i = 0; i < NUM_BUTTONS; i++) {{
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
    previous[i] = digitalRead(BUTTON_PINS[i]);
  }}
}}

void loop() {{
  for (int i = 0; i < NUM_BUTTONS; i++) {{
    int current = digitalRead(BUTTON_PINS[i]);
    if (current != previous[i]) {{
      Serial.print("BUTTON ");
      Serial.print(i + 1);
      Serial.println(current == LOW ? " PRESSED" : " RELEASED");
      previous[i] = current;
    }}
  }}
  delay(20);
}}
"""

    if test_name == "OLED display":
        rotation = OLED_ROTATIONS.get(hw.get("oled_rotation", "0 degrees"), "U8G2_R0")
        address = int(str(hw["oled_address"]), 16)
        return f"""\
/* Standalone OLED test. This draws a border, text, and an animated bar. */
#include <Wire.h>
#include <U8g2lib.h>

#define OLED_SDA_PIN {int(hw["oled_sda"])}
#define OLED_SCL_PIN {int(hw["oled_scl"])}
#define OLED_I2C_ADDR 0x{address:02X}
U8G2_SSD1306_128X64_NONAME_F_HW_I2C display({rotation}, U8X8_PIN_NONE);

void setup() {{
  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  display.begin();
  display.setI2CAddress(OLED_I2C_ADDR << 1);
}}

void loop() {{
  static int width = 0;
  display.clearBuffer();
  display.drawFrame(0, 0, 128, 64);
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(8, 12, "OLED TEST");
  display.drawFrame(8, 28, 112, 16);
  display.drawBox(10, 30, width, 12);
  display.sendBuffer();
  width += 4;
  if (width > 108) width = 0;
  delay(80);
}}
"""

    raise ValueError(f"Unknown hardware test: {test_name}")


def _build_draw_functions(oled: dict, n_ch: int) -> str:
    layout = oled.get("layout", "Meters")
    meter_style = oled.get("meter_style", "Vertical")
    widget_layout = oled.get("widget_layout", "Five channels")

    if widget_layout == "Three channels":
        max_shown = min(3, n_ch)
    elif widget_layout == "Status only":
        max_shown = 0
    else:
        max_shown = n_ch

    common_header = f"""\
bool channelShown(int i) {{
  if (i >= {max_shown}) return false;
  return CH_VISIBLE[i] && CH_STYLE[i] != 2;
}}

void drawChrome() {{
  if (SHOW_TITLE && strlen(OLED_TITLE) > 0) {{
    u8g2.setFont(u8g2_font_6x10_tf);
    u8g2.drawStr(2, 9, OLED_TITLE);
    u8g2.drawHLine(0, 11, 128);
  }}
  if (SHOW_FOOTER && strlen(OLED_FOOTER) > 0) {{
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.drawStr(2, 63, OLED_FOOTER);
  }}
  if (SHOW_STATUS) {{
    u8g2.setFont(u8g2_font_5x7_tf);
    if (calibrating) {{
      u8g2.drawStr(80, 9, "CAL");
    }} else if (SHOW_MUTE_INDICATOR && allMuted) {{
      u8g2.drawStr(90, 9, "MUTE");
    }}
  }}
}}
"""

    top_y = "SHOW_TITLE ? 14 : 2"
    bottom_y = "SHOW_FOOTER ? 60 : 63"

    if widget_layout == "Status only" or layout == "Status":
        # "Status only" and "Status" layout: just chrome + status line, no
        # per-channel meters at all.
        draw_body = f"""\
void drawLayout() {{
  drawChrome();
  u8g2.setFont(u8g2_font_6x10_tf);
  int y = ({top_y}) + 10;
  if (allMuted) {{
    u8g2.drawStr(4, y, "Status: MUTED");
  }} else if (calibrating) {{
    u8g2.drawStr(4, y, "Status: Calibrating...");
  }} else {{
    u8g2.drawStr(4, y, "Status: OK");
  }}
}}
"""
        return common_header + "\n" + draw_body

    if layout == "Minimal":
        draw_body = f"""\
void drawLayout() {{
  drawChrome();
  u8g2.setFont(u8g2_font_6x10_tf);
  int y = {top_y};
  char buf[24];
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    if (!channelShown(i)) continue;
    int v = mapChannel(i, smoothed[i]);
    int pct = (v * 100) / 1023;
    if (SHOW_LABELS) {{
      snprintf(buf, sizeof(buf), "%s %3d%%", CH_LABEL[i], pct);
    }} else {{
      snprintf(buf, sizeof(buf), "%3d%%", pct);
    }}
    u8g2.drawStr(2, y, buf);
    y += 10;
    if (y > ({bottom_y}) - 2) break;
  }}
}}
"""
        return common_header + "\n" + draw_body

    if layout == "Numeric":
        draw_body = f"""\
void drawLayout() {{
  drawChrome();
  u8g2.setFont(u8g2_font_6x10_tf);
  int y = {top_y};
  char buf[24];
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    if (!channelShown(i)) continue;
    int v = mapChannel(i, smoothed[i]);
    if (SHOW_LABELS) {{
      snprintf(buf, sizeof(buf), "%-5s %4d", CH_LABEL[i], v);
    }} else {{
      snprintf(buf, sizeof(buf), "%4d", v);
    }}
    if (channelMuted[i] || allMuted) {{
      u8g2.drawStr(2, y, "M");
      u8g2.drawStr(12, y, buf);
    }} else {{
      u8g2.drawStr(2, y, buf);
    }}
    y += 10;
    if (y > ({bottom_y}) - 2) break;
  }}
}}
"""
        return common_header + "\n" + draw_body

    # Default: "Meters" layout, Vertical or Horizontal bars, with optional
    # master meter when SHOW_MASTER / widget_layout == "Master + channels".
    show_master_widget = "true" if (oled.get("show_master") or widget_layout == "Master + channels") else "false"

    if meter_style == "Horizontal":
        draw_body = f"""\
void drawLayout() {{
  drawChrome();
  int top = {top_y};
  int bottom = {bottom_y};
  int usable = bottom - top;
  int rows = 0;
  for (int i = 0; i < NUM_CHANNELS; i++) if (channelShown(i)) rows++;
  if (rows == 0) rows = 1;
  int rowH = usable / rows;
  if (rowH < 8) rowH = 8;

  int y = top;
  u8g2.setFont(u8g2_font_5x7_tf);
  for (int i = 0; i < NUM_CHANNELS; i++) {{
    if (!channelShown(i)) continue;
    int v = mapChannel(i, smoothed[i]);
    int barMax = 80;
    int barW = (v * barMax) / 1023;
    int labelW = SHOW_LABELS ? 20 : 0;
    if (SHOW_LABELS) u8g2.drawStr(2, y + rowH - 2, CH_LABEL[i]);
    u8g2.drawFrame(2 + labelW, y, barMax, rowH - 2);
    if (!(channelMuted[i] || allMuted)) {{
      u8g2.drawBox(2 + labelW, y, barW, rowH - 2);
    }}
    if (SHOW_NUMERIC_VALUES) {{
      char buf[8];
      snprintf(buf, sizeof(buf), "%d", v);
      u8g2.drawStr(2 + labelW + barMax + 3, y + rowH - 2, buf);
    }}
    y += rowH;
  }}
}}
"""
        return common_header + "\n" + draw_body

    # Vertical bars (default)
    draw_body = f"""\
void drawLayout() {{
  drawChrome();
  int top = {top_y};
  int bottom = {bottom_y};
  int labelH = SHOW_LABELS ? 8 : 0;
  int barTop = top;
  int barBottom = bottom - labelH;
  int barMaxH = barBottom - barTop;
  if (barMaxH < 4) barMaxH = 4;

  bool drawMaster = {show_master_widget};
  int cols = 0;
  for (int i = 0; i < NUM_CHANNELS; i++) if (channelShown(i)) cols++;
  if (drawMaster) cols += 1;
  if (cols == 0) cols = 1;
  int colW = 126 / cols;
  int x = 2;

  u8g2.setFont(u8g2_font_5x7_tf);

  if (drawMaster) {{
    long sum = 0; int n = 0;
    for (int i = 0; i < NUM_CHANNELS; i++) {{
      if (!channelShown(i)) continue;
      sum += mapChannel(i, smoothed[i]);
      n++;
    }}
    int avg = (n > 0) ? (int)(sum / n) : 0;
    int barH = (avg * barMaxH) / 1023;
    u8g2.drawFrame(x, barBottom - barMaxH, colW - 3, barMaxH);
    u8g2.drawBox(x, barBottom - barH, colW - 3, barH);
    if (SHOW_LABELS) u8g2.drawStr(x, bottom, "MSTR");
    x += colW;
  }}

  for (int i = 0; i < NUM_CHANNELS; i++) {{
    if (!channelShown(i)) continue;
    int v = mapChannel(i, smoothed[i]);
    int barH = (v * barMaxH) / 1023;
    bool muted = channelMuted[i] || allMuted;

    u8g2.drawFrame(x, barBottom - barMaxH, colW - 3, barMaxH);
    if (!muted) {{
      u8g2.drawBox(x, barBottom - barH, colW - 3, barH);
    }}
    if (SHOW_PEAK_INDICATOR && v > 950 && !muted) {{
      u8g2.drawBox(x, barBottom - barMaxH, colW - 3, 2);
    }}
    if (SHOW_LABELS) {{
      u8g2.drawStr(x, bottom, CH_LABEL[i]);
    }}
    if (SHOW_NUMERIC_VALUES && CH_STYLE[i] != 2) {{
      char buf[8];
      snprintf(buf, sizeof(buf), "%d", v);
      u8g2.setFont(u8g2_font_4x6_tf);
      u8g2.drawStr(x, barBottom - barMaxH - 1, buf);
      u8g2.setFont(u8g2_font_5x7_tf);
    }}
    x += colW;
  }}
}}
"""
    return common_header + "\n" + draw_body
