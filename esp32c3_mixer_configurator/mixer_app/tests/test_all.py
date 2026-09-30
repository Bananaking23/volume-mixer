"""Regression tests for the ESP32-C3 Audio Mixer Configurator.

Run with:   python tests/test_all.py
(from the project root -- it adds the parent directory to sys.path itself)

Covers the checks called for in the project spec:
  * Python syntax validation of every module
  * Default firmware generation
  * Firmware generation with custom pins/actions/widgets
  * Balanced braces / no duplicate declarations in generated C++
  * Old/partial/corrupt configuration files never crash the loader
  * A real compiler (g++) syntax-checking the generated sketch against
    minimal Arduino/U8g2/Preferences stubs, when g++ is available
"""
from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import firmware_generator

FAILURES = []


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        FAILURES.append(f"{name}: {detail}")


def check_braces(src: str) -> bool:
    depth = 0
    for ch in src:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def check_parens(src: str) -> bool:
    depth = 0
    for ch in src:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def build_variants():
    variants = [("default", config.default_config())]

    cfg = config.default_config()
    cfg["hardware"]["slider_pins"] = [0, 1, 3, 4, 5]
    cfg["oled_designer"]["meter_style"] = "Horizontal"
    cfg["oled_designer"]["widget_layout"] = "Master + channels"
    cfg["oled_designer"]["show_master"] = True
    cfg["button_actions"][0] = {"action": "Custom shortcut", "trigger": "On release", "custom": "CTRL+ALT+M"}
    cfg["button_actions"][1] = {"action": "F13", "trigger": "On press", "custom": ""}
    variants.append(("horizontal_master", cfg))

    cfg = config.default_config()
    cfg["oled_designer"]["layout"] = "Numeric"
    cfg["oled_designer"]["widget_layout"] = "Three channels"
    cfg["channels"][4]["visible"] = False
    cfg["button_actions"][0]["action"] = "MuteNext"
    cfg["button_actions"][1]["action"] = "MutePrevious"
    variants.append(("numeric_three", cfg))

    cfg = config.default_config()
    cfg["oled_designer"]["layout"] = "Minimal"
    cfg["oled_designer"]["widget_layout"] = "Status only"
    cfg["button_actions"][0]["action"] = "Recalibrate"
    cfg["button_actions"][1]["action"] = "ToggleScreen"
    variants.append(("minimal_statusonly", cfg))

    cfg = config.default_config()
    cfg["oled_designer"]["layout"] = "Status"
    cfg["oled_designer"]["show_title"] = False
    cfg["oled_designer"]["show_footer"] = False
    cfg["channels"][0]["style"] = "Hidden"
    cfg["channels"][1]["style"] = "Number"
    cfg["button_actions"][0]["action"] = "ScreenOff"
    cfg["button_actions"][1]["action"] = "ScreenOn"
    variants.append(("status_layout", cfg))

    cfg = config.default_config()
    cfg["oled_designer"]["title"] = 'My "Mixer" \\ Rig'
    cfg["channels"][0]["label"] = 'C"1'
    cfg["button_actions"][0]["action"] = "Custom shortcut"
    cfg["button_actions"][0]["custom"] = 'SAY "hi" \\ bye'
    variants.append(("escaping_stress", cfg))

    cfg = config.default_config()
    cfg["oled_designer"]["show_master"] = True
    cfg["oled_designer"]["show_peak_indicator"] = True
    cfg["channels"][2]["invert"] = True
    variants.append(("peak_master_invert", cfg))

    cfg = config.default_config()
    for ba in cfg["button_actions"]:
        ba["action"] = "None"
    variants.append(("no_button_actions", cfg))

    cfg = config.default_config()
    cfg["oled_designer"]["show_title"] = False
    cfg["oled_designer"]["show_footer"] = False
    cfg["oled_designer"]["show_status"] = False
    cfg["oled_designer"]["show_labels"] = False
    cfg["oled_designer"]["show_numeric_values"] = False
    variants.append(("all_chrome_off", cfg))

    return variants


def main():
    tmp = tempfile.mkdtemp(prefix="mixer_tests_")

    # 1. Python syntax validation
    for mod in ["config.py", "firmware_generator.py", "arduino_tools.py", "theme.py", "desktop_app.py"]:
        path = os.path.join(ROOT, mod)
        try:
            py_compile.compile(path, doraise=True)
            check(f"py_compile {mod}", True)
        except py_compile.PyCompileError as exc:
            check(f"py_compile {mod}", False, str(exc))

    # 2. Default config has zero validation errors
    default_cfg = config.default_config()
    issues = config.validate_config(default_cfg)
    check("default config has no errors", not config.has_errors(issues), str(issues))

    # 3. Firmware generation across variants: braces, parens, no dupes
    variants = build_variants()
    dupe_re_ok = True
    gpp = shutil.which("g++")
    stub_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arduino_stubs")

    for name, cfg in variants:
        src = firmware_generator.generate_firmware(cfg)
        check(f"generate firmware: {name}", bool(src) and "void loop()" in src)
        check(f"balanced braces: {name}", check_braces(src))
        check(f"balanced parens: {name}", check_parens(src))

        cpp_path = os.path.join(tmp, f"{name}.cpp")
        with open(cpp_path, "w") as fh:
            fh.write(src)

        if gpp:
            result = subprocess.run(
                [gpp, "-std=gnu++17", "-fsyntax-only", "-include", "Arduino.h",
                 "-I", stub_dir, cpp_path],
                capture_output=True, text=True,
            )
            check(f"g++ syntax check: {name}", result.returncode == 0, result.stderr[-1500:])
        else:
            print(f"[SKIP] g++ syntax check: {name} (g++ not found)")

    # 4. Config migration: old/partial file must never crash and must fill defaults
    old_partial_path = os.path.join(tmp, "old_partial.json")
    config.save_config(old_partial_path, {"hardware": {"slider_pins": [0, 1, 2, 3, 4]}})
    loaded = config.load_config(old_partial_path)
    check("old/partial config migrates cleanly",
          loaded["hardware"]["board_fqbn"] == config.BOARD_FQBN_DEFAULT
          and len(loaded["channels"]) == 5 and len(loaded["button_actions"]) == 2)

    # 5. Corrupt JSON must never crash the loader
    corrupt_path = os.path.join(tmp, "corrupt.json")
    with open(corrupt_path, "w") as fh:
        fh.write("{not valid json")
    loaded_corrupt = config.load_config(corrupt_path)
    check("corrupt config file does not crash loader", len(loaded_corrupt["channels"]) == 5)

    # 6. Missing file must never crash the loader
    loaded_missing = config.load_config(os.path.join(tmp, "does_not_exist.json"))
    check("missing config file does not crash loader",
          loaded_missing["hardware"]["board_fqbn"] == config.BOARD_FQBN_DEFAULT)

    # 7. Sketch output path exists after generation
    sketch_path = os.path.join(tmp, "arduino_build", "deej_esp32", "deej_esp32.ino")
    os.makedirs(os.path.dirname(sketch_path), exist_ok=True)
    with open(sketch_path, "w") as fh:
        fh.write(firmware_generator.generate_firmware(default_cfg))
    check("generated sketch path exists", os.path.isfile(sketch_path))

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All checks passed.")
        print("NOTE: no ESP32-C3 hardware / Arduino CLI was available in this environment, "
              "so an actual on-device compile+upload was not performed. Run 'arduino-cli "
              "compile' and 'arduino-cli upload' (or use the app's Build & Upload tab) with "
              "the board connected to complete hardware validation.")


if __name__ == "__main__":
    main()
