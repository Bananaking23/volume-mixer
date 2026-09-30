"""Configuration schema, defaults, persistence and validation for the
ESP32-C3 Audio Mixer Configurator.

All application state lives in a single JSON-serialisable dict produced by
default_config(). load_config()/save_config() handle disk I/O, and
load_config() safely migrates old files by deep-merging them onto the
current defaults so that missing fields never crash the app.
"""
from __future__ import annotations

import copy
import json
import os
import re

CONFIG_VERSION = 2

# ---------------------------------------------------------------------------
# Hardware knowledge base (ESP32-C3 / ESP32-C3 SuperMini)
# ---------------------------------------------------------------------------

# ADC1 channels on the ESP32-C3 map 1:1 to GPIO0-GPIO4. GPIO5 is ADC2, which
# is unreliable once Wi-Fi/BT is active, so it is intentionally left out of
# the "recommended" set (using it only produces a warning, not an error).
ADC_CAPABLE_PINS = {0, 1, 2, 3, 4}

# Boot-mode strapping pins. Pulling these low/high at boot changes how the
# chip starts up, so using them for sliders/buttons is risky.
STRAPPING_PINS = {2, 8, 9}

# Native-USB D-/D+ pins. Repurposing these breaks USB (and therefore
# flashing + the serial monitor) on most ESP32-C3 boards.
USB_PINS = {18, 19}

# Commonly wired internally to the module's SPI flash and not usable as
# general purpose IO on most ESP32-C3 modules (including the SuperMini).
RESERVED_FLASH_PINS = {11, 12, 13, 14, 15, 16, 17}

VALID_GPIO_RANGE = range(0, 22)

HARDWARE_PROFILES = {
    "SuperMini (SDA=8 / SCL=9)": {"oled_sda": 8, "oled_scl": 9},
    "SuperMini (SDA=6 / SCL=7)": {"oled_sda": 6, "oled_scl": 7},
    "Custom": {},
}

BOARD_FQBN_DEFAULT = "esp32:esp32:esp32c3"

BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
LED_STRIP_MODES = ["Per-channel levels", "Master level", "Solid color", "Rainbow cycle"]
LED_TESTS = ["Full sequence", "Red", "Green", "Blue", "Low color", "High color", "White chase", "Rainbow cycle"]
HARDWARE_TESTS = [
    "LED strip - Full sequence", "LED strip - Red", "LED strip - Green", "LED strip - Blue",
    "LED strip - Low color", "LED strip - High color", "LED strip - White chase", "LED strip - Rainbow cycle",
    "Potentiometers", "Buttons", "OLED display",
]
BUILD_TARGETS = ["Main mixer", "LED strip test", "Potentiometers test", "Buttons test", "OLED display test"]

CHANNEL_STYLES = ["Bar", "Number", "Hidden"]
OLED_LAYOUTS = ["Meters", "Numeric", "Status", "Minimal"]
METER_STYLES = ["Vertical", "Horizontal"]
WIDGET_LAYOUTS = ["Five channels", "Three channels", "Master + channels", "Status only"]

BUTTON_ACTIONS = (
    ["None", "Recalibrate", "MuteAll", "MuteNext", "MutePrevious",
     "ScreenOn", "ScreenOff", "ToggleScreen"]
    + [f"F{n}" for n in range(13, 25)]
    + ["Custom shortcut"]
)

BUTTON_TRIGGERS = ["On press", "On release"]

OLED_ROTATIONS = {
    "0 degrees": "U8G2_R0",
    "90 degrees": "U8G2_R1",
    "180 degrees": "U8G2_R2",
    "270 degrees": "U8G2_R3",
}

NUM_SLIDERS = 5
NUM_BUTTONS = 2


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _default_channels():
    return [
        {
            "name": f"Channel {i + 1}",
            "label": f"CH{i + 1}",
            "invert": False,
            "adc_min": 0,
            "adc_max": 4095,
            "visible": True,
            "style": "Bar",
        }
        for i in range(NUM_SLIDERS)
    ]


def _default_button_actions():
    defaults = [
        {"action": "ToggleScreen", "trigger": "On press", "custom": ""},
        {"action": "MuteAll", "trigger": "On press", "custom": ""},
    ]
    return defaults[:NUM_BUTTONS]


def default_config():
    return {
        "meta": {"version": CONFIG_VERSION},
        "hardware": {
            "profile": "SuperMini (SDA=8 / SCL=9)",
            "slider_pins": [0, 1, 2, 3, 4],
            "button_pins": [10, 20],
            "oled_sda": 8,
            "oled_scl": 9,
            "oled_address": "0x3C",
            "oled_rotation": "0 degrees",
            "led_strip": {
                "enabled": False,
                "data_pin": 5,
                "led_count": 5,
                "brightness": 64,
                "mode": "Per-channel levels",
                "low_color": "#00FF00",
                "high_color": "#FF0000",
            },
            "board_fqbn": BOARD_FQBN_DEFAULT,
            "baud_rate": 115200,
            "com_port": "",
            "arduino_cli_path": "",
        },
        "channels": _default_channels(),
        "button_actions": _default_button_actions(),
        "oled_designer": {
            "title": "Audio Mixer",
            "footer": "deej",
            "show_title": True,
            "show_footer": True,
            "show_status": True,
            "show_labels": True,
            "layout": "Meters",
            "meter_style": "Vertical",
            "widget_layout": "Five channels",
            "show_master": False,
            "show_mute_indicator": True,
            "show_peak_indicator": False,
            "show_numeric_values": True,
        },
        "firmware": {
            "smoothing": 0.2,
            "update_interval_ms": 30,
        },
        "appearance": {
            "theme": "dark",
            "accent": "#4C9AFF",
            "preview_bg": "#1B1F24",
        },
    }


# ---------------------------------------------------------------------------
# Merge / migration helpers
# ---------------------------------------------------------------------------

def _deep_merge(defaults, loaded):
    """Deep-merge `loaded` onto `defaults`, keeping default values for any
    key that is missing or has the wrong shape in `loaded`."""
    if not isinstance(loaded, dict):
        return copy.deepcopy(defaults)
    merged = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_list(default_list, loaded_list, template):
    """Merge a list-of-dicts field (channels / button_actions) item by item
    so partial/old entries still get every key, and the list always keeps
    the fixed length dictated by the hardware (5 sliders, 2 buttons)."""
    if not isinstance(loaded_list, list):
        return copy.deepcopy(default_list)
    result = []
    for i in range(len(default_list)):
        item_default = default_list[i]
        item_loaded = loaded_list[i] if i < len(loaded_list) and isinstance(loaded_list[i], dict) else {}
        result.append(_deep_merge(item_default, item_loaded))
    return result


def load_config(path):
    """Load config from `path`, safely filling in any missing fields with
    defaults. Never raises on a missing/corrupt/old file."""
    defaults = default_config()
    if not path or not os.path.exists(path):
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return defaults

    merged = _deep_merge(defaults, loaded)
    merged["channels"] = _merge_list(
        defaults["channels"], loaded.get("channels"), defaults["channels"][0]
    )
    merged["button_actions"] = _merge_list(
        defaults["button_actions"], loaded.get("button_actions"), defaults["button_actions"][0]
    )
    merged.setdefault("meta", {})["version"] = CONFIG_VERSION
    return merged


def save_config(path, cfg):
    """Atomically write `cfg` to `path` as JSON."""
    cfg = copy.deepcopy(cfg)
    cfg.setdefault("meta", {})["version"] = CONFIG_VERSION
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(cfg):
    """Return a list of {level, field, message} dicts describing problems
    with `cfg`. level is 'error' (blocks generation/upload) or 'warning'
    (shown but non-blocking)."""
    issues = []

    def add(level, field, message):
        issues.append({"level": level, "field": field, "message": message})

    hw = cfg.get("hardware", {})
    slider_pins = hw.get("slider_pins", [])
    button_pins = hw.get("button_pins", [])
    sda = hw.get("oled_sda")
    scl = hw.get("oled_scl")
    led_strip = hw.get("led_strip", {})

    assigned = []
    for idx, p in enumerate(slider_pins):
        assigned.append((f"slider_pins[{idx}]", p, "slider"))
    for idx, p in enumerate(button_pins):
        assigned.append((f"button_pins[{idx}]", p, "button"))
    assigned.append(("oled_sda", sda, "i2c"))
    assigned.append(("oled_scl", scl, "i2c"))
    if led_strip.get("enabled", False):
        assigned.append(("led_strip.data_pin", led_strip.get("data_pin"), "led"))

    for field, p, kind in assigned:
        if p is None or p == "":
            add("error", field, "Pin is not set.")
            continue
        if not isinstance(p, int) or p not in VALID_GPIO_RANGE:
            add("error", field, f"GPIO{p} is not a valid ESP32-C3 pin (0-21).")
            continue
        if p in RESERVED_FLASH_PINS:
            add("error", field, f"GPIO{p} is normally wired to the module's SPI flash and cannot be used.")
        if p in USB_PINS:
            add("warning", field, f"GPIO{p} is the native-USB D+/D- pin; using it can break USB/flashing.")
        if p in STRAPPING_PINS and kind != "i2c":
            add("warning", field, f"GPIO{p} is a boot strapping pin; using it can prevent normal booting.")

    seen = {}
    for field, p, _kind in assigned:
        if isinstance(p, int):
            seen.setdefault(p, []).append(field)
    for p, fields in seen.items():
        if len(fields) > 1:
            add("error", "/".join(fields), f"GPIO{p} is assigned to more than one function ({', '.join(fields)}).")

    for idx, p in enumerate(slider_pins):
        if isinstance(p, int) and p in VALID_GPIO_RANGE and p not in ADC_CAPABLE_PINS:
            add("warning", f"slider_pins[{idx}]",
                f"GPIO{p} is not one of the recommended ADC1 pins (0-4); readings may be unreliable.")

    addr = str(hw.get("oled_address", ""))
    if not re.fullmatch(r"0x[0-9A-Fa-f]{2}", addr):
        add("error", "oled_address", "OLED I2C address must look like 0x3C.")

    if led_strip.get("enabled", False):
        led_count = led_strip.get("led_count")
        if not isinstance(led_count, int) or not 1 <= led_count <= 256:
            add("error", "led_strip.led_count", "LED count must be an integer from 1 to 256.")
        brightness = led_strip.get("brightness")
        if not isinstance(brightness, int) or not 1 <= brightness <= 255:
            add("error", "led_strip.brightness", "LED brightness must be an integer from 1 to 255.")
        if led_strip.get("mode") not in LED_STRIP_MODES:
            add("error", "led_strip.mode", "Invalid LED strip mode.")
        for color_name in ("low_color", "high_color"):
            color = str(led_strip.get(color_name, ""))
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                add("error", f"led_strip.{color_name}", f"LED {color_name.replace('_', ' ')} must look like #RRGGBB.")

    fqbn = hw.get("board_fqbn", "")
    if not fqbn:
        add("error", "board_fqbn", "Board FQBN is required.")
    elif fqbn.count(":") != 2:
        add("warning", "board_fqbn", "Board FQBN does not look valid (expected vendor:arch:board).")

    baud = hw.get("baud_rate")
    if not isinstance(baud, int) or baud <= 0:
        add("error", "baud_rate", "Baud rate must be a positive integer.")
    elif baud not in BAUD_RATES:
        add("warning", "baud_rate", "Uncommon baud rate; make sure the host app matches it.")

    for idx, ch in enumerate(cfg.get("channels", [])):
        lo, hi = ch.get("adc_min"), ch.get("adc_max")
        if not isinstance(lo, int) or not isinstance(hi, int) or not (0 <= lo < hi <= 4095):
            add("error", f"channels[{idx}]", f"'{ch.get('name', idx)}': ADC min/max must satisfy 0 <= min < max <= 4095.")
        if ch.get("style") not in CHANNEL_STYLES:
            add("error", f"channels[{idx}].style", f"'{ch.get('name', idx)}': invalid display style.")
        if not str(ch.get("label", "")).strip():
            add("warning", f"channels[{idx}].label", f"'{ch.get('name', idx)}': empty OLED label.")

    for idx, ba in enumerate(cfg.get("button_actions", [])):
        if ba.get("action") not in BUTTON_ACTIONS:
            add("error", f"button_actions[{idx}].action", f"Button {idx + 1}: invalid action.")
        if ba.get("action") == "Custom shortcut" and not str(ba.get("custom", "")).strip():
            add("error", f"button_actions[{idx}].custom", f"Button {idx + 1}: custom shortcut text is required.")
        if ba.get("trigger") not in BUTTON_TRIGGERS:
            add("error", f"button_actions[{idx}].trigger", f"Button {idx + 1}: invalid trigger mode.")

    smoothing = cfg.get("firmware", {}).get("smoothing")
    if not isinstance(smoothing, (int, float)) or not (0.0 <= smoothing < 1.0):
        add("error", "firmware.smoothing", "Smoothing must be a number between 0.0 (none) and 0.99.")

    interval = cfg.get("firmware", {}).get("update_interval_ms")
    if not isinstance(interval, int) or interval <= 0:
        add("error", "firmware.update_interval_ms", "Update interval must be a positive integer (ms).")
    elif interval < 5:
        add("warning", "firmware.update_interval_ms", "Very short update interval; may flood the serial link.")

    return issues


def has_errors(issues):
    return any(i["level"] == "error" for i in issues)
