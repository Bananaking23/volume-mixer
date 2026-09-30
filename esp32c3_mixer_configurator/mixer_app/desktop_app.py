"""ESP32-C3 Audio Mixer Configurator -- desktop app.

Lets a user configure the complete mixer device (hardware pins, per-channel
behaviour, OLED layout, button actions) without touching Arduino code, then
generate an .ino sketch and optionally compile/upload it via Arduino CLI.

Run with:  python desktop_app.py
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, scrolledtext

import arduino_tools
import config
import firmware_generator
import theme


APP_TITLE = "ESP32-C3 Audio Mixer Configurator"


_OLED_FONT_5X7 = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    "%": ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "11100"),
}
for _letter, _rows in {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}.items():
    _OLED_FONT_5X7[_letter] = _rows

_OLED_FONT_3X5 = {
    char: tuple("".join(row[index] for index in (0, 2, 4)) for row in (glyph[0], glyph[1], glyph[3], glyph[5], glyph[6]))
    for char, glyph in _OLED_FONT_5X7.items()
}


def _app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


class MixerConfiguratorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x800")
        self.minsize(1080, 700)

        self.config_path = os.path.join(_app_dir(), "mixer_config.json")
        self.cfg = config.load_config(self.config_path)

        self.palette = theme.apply_dark_theme(self, accent=self.cfg["appearance"]["accent"])
        self.configure(bg=self.palette["bg"])

        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.current_issues: list[dict] = []
        self._suppress_trace = False
        self.hw_issue_labels: dict[str, ttk.Label] = {}
        self.serial_process = None
        self.serial_status_var = tk.StringVar(value="Stopped")

        self._build_vars()
        self._build_layout()

        self._suppress_trace = False
        self.refresh_validation()
        self.redraw_oled_preview()
        self._drain_log_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------
    def _build_vars(self):
        cfg = self.cfg
        hw = cfg["hardware"]

        self.var_profile = tk.StringVar(value=hw["profile"])
        self.var_slider_pins = [tk.StringVar(value=str(p)) for p in hw["slider_pins"]]
        self.var_button_pins = [tk.StringVar(value=str(p)) for p in hw["button_pins"]]
        self.var_sda = tk.StringVar(value=str(hw["oled_sda"]))
        self.var_scl = tk.StringVar(value=str(hw["oled_scl"]))
        self.var_oled_addr = tk.StringVar(value=hw["oled_address"])
        self.var_rotation = tk.StringVar(value=hw["oled_rotation"])
        led_strip = hw["led_strip"]
        self.var_led_enabled = tk.BooleanVar(value=led_strip["enabled"])
        self.var_led_pin = tk.StringVar(value=str(led_strip["data_pin"]))
        self.var_led_count = tk.StringVar(value=str(led_strip["led_count"]))
        self.var_led_brightness = tk.StringVar(value=str(led_strip["brightness"]))
        self.var_led_mode = tk.StringVar(value=led_strip["mode"])
        self.var_led_low_color = tk.StringVar(value=led_strip["low_color"])
        self.var_led_high_color = tk.StringVar(value=led_strip["high_color"])
        self.var_fqbn = tk.StringVar(value=hw["board_fqbn"])
        self.var_baud = tk.StringVar(value=str(hw["baud_rate"]))
        self.var_com_port = tk.StringVar(value=hw["com_port"])
        self.var_cli_path = tk.StringVar(value=hw["arduino_cli_path"])

        self.channel_vars = []
        for ch in cfg["channels"]:
            self.channel_vars.append({
                "name": tk.StringVar(value=ch["name"]),
                "label": tk.StringVar(value=ch["label"]),
                "invert": tk.BooleanVar(value=ch["invert"]),
                "adc_min": tk.StringVar(value=str(ch["adc_min"])),
                "adc_max": tk.StringVar(value=str(ch["adc_max"])),
                "visible": tk.BooleanVar(value=ch["visible"]),
                "style": tk.StringVar(value=ch["style"]),
            })

        self.button_action_vars = []
        for ba in cfg["button_actions"]:
            self.button_action_vars.append({
                "action": tk.StringVar(value=ba["action"]),
                "trigger": tk.StringVar(value=ba["trigger"]),
                "custom": tk.StringVar(value=ba["custom"]),
            })

        od = cfg["oled_designer"]
        self.var_oled_title = tk.StringVar(value=od["title"])
        self.var_oled_footer = tk.StringVar(value=od["footer"])
        self.var_show_title = tk.BooleanVar(value=od["show_title"])
        self.var_show_footer = tk.BooleanVar(value=od["show_footer"])
        self.var_show_status = tk.BooleanVar(value=od["show_status"])
        self.var_show_labels = tk.BooleanVar(value=od["show_labels"])
        self.var_layout = tk.StringVar(value=od["layout"])
        self.var_meter_style = tk.StringVar(value=od["meter_style"])
        self.var_widget_layout = tk.StringVar(value=od["widget_layout"])
        self.var_show_master = tk.BooleanVar(value=od["show_master"])
        self.var_show_mute = tk.BooleanVar(value=od["show_mute_indicator"])
        self.var_show_peak = tk.BooleanVar(value=od["show_peak_indicator"])
        self.var_show_numeric = tk.BooleanVar(value=od["show_numeric_values"])

        fw = cfg["firmware"]
        self.var_smoothing = tk.StringVar(value=str(fw["smoothing"]))
        self.var_interval_ms = tk.StringVar(value=str(fw["update_interval_ms"]))

        ap = cfg["appearance"]
        self.var_accent = tk.StringVar(value=ap["accent"])
        self.var_preview_bg = tk.StringVar(value=ap["preview_bg"])

        self.top_status_var = tk.StringVar(value="")
        self.validation_summary_var = tk.StringVar(value="")
        self.var_hardware_test = tk.StringVar(value=config.HARDWARE_TESTS[0])
        self.var_build_target = tk.StringVar(value=config.BUILD_TARGETS[0])
        self.sketch_path_var = tk.StringVar(value=self._sketch_path())
        self.cli_status_var = tk.StringVar(value="Not detected yet.")

        # Wire every variable to a single change handler so validation and
        # the OLED preview always stay in sync, no matter which tab/widget
        # produced the change.
        all_vars = list(self.var_slider_pins) + list(self.var_button_pins) + [
            self.var_profile, self.var_sda, self.var_scl, self.var_oled_addr,
            self.var_rotation, self.var_led_enabled, self.var_led_pin,
            self.var_led_count, self.var_led_brightness, self.var_led_mode,
            self.var_led_low_color, self.var_led_high_color,
            self.var_fqbn, self.var_baud,
            self.var_oled_title, self.var_oled_footer,
            self.var_show_title, self.var_show_footer, self.var_show_status,
            self.var_show_labels, self.var_layout, self.var_meter_style,
            self.var_widget_layout, self.var_show_master, self.var_show_mute,
            self.var_show_peak, self.var_show_numeric,
            self.var_smoothing, self.var_interval_ms,
        ]
        for ch in self.channel_vars:
            all_vars.extend(ch.values())
        for ba in self.button_action_vars:
            all_vars.extend(ba.values())

        for v in all_vars:
            v.trace_add("write", self._on_any_change)

        self.var_profile.trace_add("write", self._on_profile_change)
        self.var_build_target.trace_add("write", self._on_build_target_change)

    def _on_build_target_change(self, *_args):
        if self.var_build_target.get() == "Main mixer":
            self.sketch_path_var.set(self._sketch_path())
        else:
            self.sketch_path_var.set(self._led_test_path())

    def _on_any_change(self, *_args):
        if self._suppress_trace:
            return
        self.refresh_validation()
        self.redraw_oled_preview()

    def _on_profile_change(self, *_args):
        if self._suppress_trace:
            return
        profile = config.HARDWARE_PROFILES.get(self.var_profile.get())
        if not profile:
            return
        self._suppress_trace = True
        try:
            if "oled_sda" in profile:
                self.var_sda.set(str(profile["oled_sda"]))
            if "oled_scl" in profile:
                self.var_scl.set(str(profile["oled_scl"]))
        finally:
            self._suppress_trace = False
        self.refresh_validation()
        self.redraw_oled_preview()

    def _apply_cfg_to_vars(self, cfg):
        self._suppress_trace = True
        try:
            hw = cfg["hardware"]
            self.var_profile.set(hw["profile"])
            for var, p in zip(self.var_slider_pins, hw["slider_pins"]):
                var.set(str(p))
            for var, p in zip(self.var_button_pins, hw["button_pins"]):
                var.set(str(p))
            self.var_sda.set(str(hw["oled_sda"]))
            self.var_scl.set(str(hw["oled_scl"]))
            self.var_oled_addr.set(hw["oled_address"])
            self.var_rotation.set(hw["oled_rotation"])
            led_strip = hw["led_strip"]
            self.var_led_enabled.set(led_strip["enabled"])
            self.var_led_pin.set(str(led_strip["data_pin"]))
            self.var_led_count.set(str(led_strip["led_count"]))
            self.var_led_brightness.set(str(led_strip["brightness"]))
            self.var_led_mode.set(led_strip["mode"])
            self.var_led_low_color.set(led_strip["low_color"])
            self.var_led_high_color.set(led_strip["high_color"])
            self.var_fqbn.set(hw["board_fqbn"])
            self.var_baud.set(str(hw["baud_rate"]))
            self.var_com_port.set(hw["com_port"])
            self.var_cli_path.set(hw["arduino_cli_path"])

            for vars_, ch in zip(self.channel_vars, cfg["channels"]):
                vars_["name"].set(ch["name"])
                vars_["label"].set(ch["label"])
                vars_["invert"].set(ch["invert"])
                vars_["adc_min"].set(str(ch["adc_min"]))
                vars_["adc_max"].set(str(ch["adc_max"]))
                vars_["visible"].set(ch["visible"])
                vars_["style"].set(ch["style"])

            for vars_, ba in zip(self.button_action_vars, cfg["button_actions"]):
                vars_["action"].set(ba["action"])
                vars_["trigger"].set(ba["trigger"])
                vars_["custom"].set(ba["custom"])

            od = cfg["oled_designer"]
            self.var_oled_title.set(od["title"])
            self.var_oled_footer.set(od["footer"])
            self.var_show_title.set(od["show_title"])
            self.var_show_footer.set(od["show_footer"])
            self.var_show_status.set(od["show_status"])
            self.var_show_labels.set(od["show_labels"])
            self.var_layout.set(od["layout"])
            self.var_meter_style.set(od["meter_style"])
            self.var_widget_layout.set(od["widget_layout"])
            self.var_show_master.set(od["show_master"])
            self.var_show_mute.set(od["show_mute_indicator"])
            self.var_show_peak.set(od["show_peak_indicator"])
            self.var_show_numeric.set(od["show_numeric_values"])

            fw = cfg["firmware"]
            self.var_smoothing.set(str(fw["smoothing"]))
            self.var_interval_ms.set(str(fw["update_interval_ms"]))

            ap = cfg["appearance"]
            self.var_accent.set(ap["accent"])
            self.var_preview_bg.set(ap["preview_bg"])
        finally:
            self._suppress_trace = False
        self.refresh_validation()
        self.redraw_oled_preview()

    # ------------------------------------------------------------------
    # Collecting UI state back into a plain config dict
    # ------------------------------------------------------------------
    @staticmethod
    def _to_int(text, default=0):
        try:
            return int(str(text).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(text, default=0.0):
        try:
            return float(str(text).strip())
        except (TypeError, ValueError):
            return default

    def collect(self) -> dict:
        cfg = config.default_config()

        hw = cfg["hardware"]
        hw["profile"] = self.var_profile.get()
        hw["slider_pins"] = [self._to_int(v.get(), -1) for v in self.var_slider_pins]
        hw["button_pins"] = [self._to_int(v.get(), -1) for v in self.var_button_pins]
        hw["oled_sda"] = self._to_int(self.var_sda.get(), -1)
        hw["oled_scl"] = self._to_int(self.var_scl.get(), -1)
        hw["oled_address"] = self.var_oled_addr.get().strip()
        hw["oled_rotation"] = self.var_rotation.get()
        hw["led_strip"] = {
            "enabled": bool(self.var_led_enabled.get()),
            "data_pin": self._to_int(self.var_led_pin.get(), -1),
            "led_count": self._to_int(self.var_led_count.get(), 0),
            "brightness": self._to_int(self.var_led_brightness.get(), 0),
            "mode": self.var_led_mode.get(),
            "low_color": self.var_led_low_color.get().strip().upper(),
            "high_color": self.var_led_high_color.get().strip().upper(),
        }
        hw["board_fqbn"] = self.var_fqbn.get().strip()
        hw["baud_rate"] = self._to_int(self.var_baud.get(), 0)
        hw["com_port"] = self.var_com_port.get().strip()
        hw["arduino_cli_path"] = self.var_cli_path.get().strip()

        channels = []
        for vars_ in self.channel_vars:
            channels.append({
                "name": vars_["name"].get().strip() or "Channel",
                "label": vars_["label"].get().strip(),
                "invert": bool(vars_["invert"].get()),
                "adc_min": self._to_int(vars_["adc_min"].get(), 0),
                "adc_max": self._to_int(vars_["adc_max"].get(), 4095),
                "visible": bool(vars_["visible"].get()),
                "style": vars_["style"].get(),
            })
        cfg["channels"] = channels

        button_actions = []
        for vars_ in self.button_action_vars:
            button_actions.append({
                "action": vars_["action"].get(),
                "trigger": vars_["trigger"].get(),
                "custom": vars_["custom"].get().strip(),
            })
        cfg["button_actions"] = button_actions

        od = cfg["oled_designer"]
        od["title"] = self.var_oled_title.get().strip()
        od["footer"] = self.var_oled_footer.get().strip()
        od["show_title"] = bool(self.var_show_title.get())
        od["show_footer"] = bool(self.var_show_footer.get())
        od["show_status"] = bool(self.var_show_status.get())
        od["show_labels"] = bool(self.var_show_labels.get())
        od["layout"] = self.var_layout.get()
        od["meter_style"] = self.var_meter_style.get()
        od["widget_layout"] = self.var_widget_layout.get()
        od["show_master"] = bool(self.var_show_master.get())
        od["show_mute_indicator"] = bool(self.var_show_mute.get())
        od["show_peak_indicator"] = bool(self.var_show_peak.get())
        od["show_numeric_values"] = bool(self.var_show_numeric.get())

        fw = cfg["firmware"]
        fw["smoothing"] = self._to_float(self.var_smoothing.get(), 0.2)
        fw["update_interval_ms"] = self._to_int(self.var_interval_ms.get(), 30)

        ap = cfg["appearance"]
        ap["accent"] = self.var_accent.get()
        ap["preview_bg"] = self.var_preview_bg.get()

        return cfg

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self):
        self._build_topbar()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(6, 0))

        hardware_tab = ttk.Frame(self.notebook, padding=14)
        channels_tab = ttk.Frame(self.notebook, padding=14)
        oled_tab = ttk.Frame(self.notebook, padding=14)
        tests_tab = ttk.Frame(self.notebook, padding=14)
        build_tab = ttk.Frame(self.notebook, padding=14)

        self.notebook.add(hardware_tab, text="Hardware")
        self.notebook.add(channels_tab, text="Channels & Actions")
        self.notebook.add(oled_tab, text="OLED Designer")
        self.notebook.add(tests_tab, text="Hardware Tests")
        self.notebook.add(build_tab, text="Build & Upload")

        self._build_hardware_tab(hardware_tab)
        self._build_channels_tab(channels_tab)
        self._build_oled_tab(oled_tab)
        self._build_tests_tab(tests_tab)
        self._build_build_tab(build_tab)

        self._build_bottom_bar()

    def _build_topbar(self):
        bar = ttk.Frame(self, padding=(18, 14, 18, 10))
        bar.pack(fill="x")

        title_group = ttk.Frame(bar)
        title_group.pack(side="left")
        ttk.Label(title_group, text=APP_TITLE, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(title_group, text="Configure, test, and upload your ESP32-C3 mixer.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(bar, textvariable=self.top_status_var, style="Status.TLabel").pack(side="left", padx=(18, 0))

        ttk.Button(bar, text="Appearance...", command=self.open_appearance_dialog).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="Load...", command=self.action_load).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="Save", command=self.action_save).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="Quick Setup", command=self.action_quick_setup).pack(side="right", padx=(6, 0))

    # -- Hardware tab ---------------------------------------------------
    def _build_hardware_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")

        # Profile
        prof = ttk.Labelframe(left, text="Hardware profile", padding=10)
        prof.pack(fill="x", pady=(0, 12))
        ttk.Label(prof, text="Profile").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(prof, textvariable=self.var_profile, state="readonly",
                           values=list(config.HARDWARE_PROFILES.keys()), width=28)
        cb.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(prof, text="Selecting a profile fills in the OLED SDA/SCL pins for that wiring.",
                  style="Dim.TLabel", wraplength=340).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Sliders
        sliders = ttk.Labelframe(left, text="Slider pins (ADC1: GPIO0-4 recommended)", padding=10)
        sliders.pack(fill="x", pady=(0, 12))
        for i, var in enumerate(self.var_slider_pins):
            ttk.Label(sliders, text=f"Slider {i + 1}").grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(sliders, textvariable=var, width=6).grid(row=i, column=1, sticky="w", padx=8)
            lbl = ttk.Label(sliders, text="", style="Warning.TLabel", wraplength=260, justify="left")
            lbl.grid(row=i, column=2, sticky="w")
            self.hw_issue_labels[f"slider_pins[{i}]"] = lbl

        # Buttons
        buttons = ttk.Labelframe(left, text="Button pins", padding=10)
        buttons.pack(fill="x", pady=(0, 12))
        for i, var in enumerate(self.var_button_pins):
            ttk.Label(buttons, text=f"Button {i + 1}").grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(buttons, textvariable=var, width=6).grid(row=i, column=1, sticky="w", padx=8)
            lbl = ttk.Label(buttons, text="", style="Warning.TLabel", wraplength=260, justify="left")
            lbl.grid(row=i, column=2, sticky="w")
            self.hw_issue_labels[f"button_pins[{i}]"] = lbl

        # OLED I2C
        oled = ttk.Labelframe(right, text="OLED (I2C, SSD1306-compatible)", padding=10)
        oled.pack(fill="x", pady=(0, 12))
        ttk.Label(oled, text="SDA pin").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(oled, textvariable=self.var_sda, width=6).grid(row=0, column=1, sticky="w", padx=8)
        self.hw_issue_labels["oled_sda"] = ttk.Label(oled, text="", style="Warning.TLabel", wraplength=260)
        self.hw_issue_labels["oled_sda"].grid(row=0, column=2, sticky="w")

        ttk.Label(oled, text="SCL pin").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(oled, textvariable=self.var_scl, width=6).grid(row=1, column=1, sticky="w", padx=8)
        self.hw_issue_labels["oled_scl"] = ttk.Label(oled, text="", style="Warning.TLabel", wraplength=260)
        self.hw_issue_labels["oled_scl"].grid(row=1, column=2, sticky="w")

        ttk.Label(oled, text="I2C address").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(oled, textvariable=self.var_oled_addr, width=8).grid(row=2, column=1, sticky="w", padx=8)
        self.hw_issue_labels["oled_address"] = ttk.Label(oled, text="", style="Warning.TLabel", wraplength=260)
        self.hw_issue_labels["oled_address"].grid(row=2, column=2, sticky="w")

        ttk.Label(oled, text="Rotation").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Combobox(oled, textvariable=self.var_rotation, state="readonly", width=14,
                     values=list(config.OLED_ROTATIONS.keys())).grid(row=3, column=1, columnspan=2, sticky="w", padx=8)

        # Addressable LED strip
        leds = ttk.Labelframe(right, text="LED strip (WS2812 / NeoPixel)", padding=10)
        leds.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(leds, text="Enable LED strip", variable=self.var_led_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(leds, text="Data pin").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(leds, textvariable=self.var_led_pin, width=6).grid(row=1, column=1, sticky="w", padx=8)
        self.hw_issue_labels["led_strip.data_pin"] = ttk.Label(leds, text="", style="Warning.TLabel", wraplength=260)
        self.hw_issue_labels["led_strip.data_pin"].grid(row=1, column=2, sticky="w")
        ttk.Label(leds, text="LED count").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Spinbox(leds, from_=1, to=256, textvariable=self.var_led_count, width=6).grid(
            row=2, column=1, sticky="w", padx=8)
        ttk.Label(leds, text="Brightness").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Spinbox(leds, from_=1, to=255, textvariable=self.var_led_brightness, width=6).grid(
            row=3, column=1, sticky="w", padx=8)
        ttk.Label(leds, text="Display mode").grid(row=4, column=0, sticky="w", pady=2)
        ttk.Combobox(leds, textvariable=self.var_led_mode, state="readonly", width=20,
                     values=config.LED_STRIP_MODES).grid(row=4, column=1, sticky="w", padx=8)
        ttk.Label(leds, text="Low color").grid(row=5, column=0, sticky="w", pady=2)
        ttk.Label(leds, textvariable=self.var_led_low_color, width=9).grid(row=5, column=1, sticky="w", padx=8)
        ttk.Button(leds, text="Choose...",
                   command=lambda: self._choose_led_color(self.var_led_low_color, "LED low color")).grid(
                       row=5, column=2, sticky="w")
        ttk.Label(leds, text="High color").grid(row=6, column=0, sticky="w", pady=2)
        ttk.Label(leds, textvariable=self.var_led_high_color, width=9).grid(row=6, column=1, sticky="w", padx=8)
        ttk.Button(leds, text="Choose...",
                   command=lambda: self._choose_led_color(self.var_led_high_color, "LED high color")).grid(
                       row=6, column=2, sticky="w")
        ttk.Label(leds, text="Requires the Adafruit NeoPixel library when compiling the sketch.",
                  style="Dim.TLabel", wraplength=420).grid(row=7, column=0, columnspan=3,
                                                            sticky="w", pady=(8, 0))

        # Board / serial
        board = ttk.Labelframe(right, text="Board & serial", padding=10)
        board.pack(fill="x", pady=(0, 12))
        ttk.Label(board, text="Board FQBN").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(board, textvariable=self.var_fqbn, width=22).grid(row=0, column=1, sticky="w", padx=8)
        self.hw_issue_labels["board_fqbn"] = ttk.Label(board, text="", style="Warning.TLabel", wraplength=260)
        self.hw_issue_labels["board_fqbn"].grid(row=0, column=2, sticky="w")

        ttk.Label(board, text="Baud rate").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(board, textvariable=self.var_baud, width=10,
                     values=[str(b) for b in config.BAUD_RATES]).grid(row=1, column=1, sticky="w", padx=8)
        self.hw_issue_labels["baud_rate"] = ttk.Label(board, text="", style="Warning.TLabel", wraplength=260)
        self.hw_issue_labels["baud_rate"].grid(row=1, column=2, sticky="w")

        ttk.Label(board, text="Tip: pin numbers are ESP32-C3 GPIO numbers, not physical header positions.",
                  style="Dim.TLabel", wraplength=420).grid(row=2, column=0, columnspan=3,
                                                            sticky="w", pady=(8, 0))

    # -- Channels & Actions tab -----------------------------------------
    def _build_channels_tab(self, parent):
        chan_frame = ttk.Labelframe(parent, text="Channels (5 sliders)", padding=10)
        chan_frame.pack(fill="x", pady=(0, 14))

        headers = ["Name", "OLED label", "Invert", "ADC min", "ADC max", "Visible", "Display style"]
        for c, h in enumerate(headers):
            ttk.Label(chan_frame, text=h, style="Dim.TLabel").grid(row=0, column=c, padx=6, pady=(0, 4), sticky="w")

        for i, vars_ in enumerate(self.channel_vars):
            r = i + 1
            ttk.Entry(chan_frame, textvariable=vars_["name"], width=16).grid(row=r, column=0, padx=6, pady=3, sticky="w")
            ttk.Entry(chan_frame, textvariable=vars_["label"], width=8).grid(row=r, column=1, padx=6, sticky="w")
            ttk.Checkbutton(chan_frame, variable=vars_["invert"]).grid(row=r, column=2, padx=6)
            ttk.Spinbox(chan_frame, from_=0, to=4094, textvariable=vars_["adc_min"], width=6).grid(row=r, column=3, padx=6)
            ttk.Spinbox(chan_frame, from_=1, to=4095, textvariable=vars_["adc_max"], width=6).grid(row=r, column=4, padx=6)
            ttk.Checkbutton(chan_frame, variable=vars_["visible"]).grid(row=r, column=5, padx=6)
            ttk.Combobox(chan_frame, textvariable=vars_["style"], state="readonly", width=10,
                         values=config.CHANNEL_STYLES).grid(row=r, column=6, padx=6)

        ttk.Label(parent,
                  text="ADC min/max calibrate the raw 0-4095 slider range to 0-1023 deej output. "
                       "\"Recalibrate\" (button action) overwrites these at runtime and saves them to the board.",
                  style="Dim.TLabel", wraplength=900).pack(anchor="w", pady=(0, 14))

        btn_frame = ttk.Labelframe(parent, text="Button actions", padding=10)
        btn_frame.pack(fill="x")

        headers2 = ["Button", "Action", "Trigger", "Custom shortcut"]
        for c, h in enumerate(headers2):
            ttk.Label(btn_frame, text=h, style="Dim.TLabel").grid(row=0, column=c, padx=6, pady=(0, 4), sticky="w")

        for i, vars_ in enumerate(self.button_action_vars):
            r = i + 1
            ttk.Label(btn_frame, text=f"Button {i + 1}").grid(row=r, column=0, padx=6, pady=4, sticky="w")
            ttk.Combobox(btn_frame, textvariable=vars_["action"], state="readonly", width=18,
                         values=config.BUTTON_ACTIONS).grid(row=r, column=1, padx=6)
            ttk.Combobox(btn_frame, textvariable=vars_["trigger"], state="readonly", width=12,
                         values=config.BUTTON_TRIGGERS).grid(row=r, column=2, padx=6)
            entry = ttk.Entry(btn_frame, textvariable=vars_["custom"], width=22)
            entry.grid(row=r, column=3, padx=6)

            def make_updater(entry=entry, var=vars_["action"]):
                def update(*_a):
                    entry.configure(state="normal" if var.get() == "Custom shortcut" else "disabled")
                return update
            updater = make_updater()
            vars_["action"].trace_add("write", updater)
            updater()

        ttk.Label(btn_frame,
                  text='Examples: "CTRL+ALT+M", "SHIFT+F13", "MEDIA_PLAY_PAUSE". F13-F24 and custom shortcuts are '
                       "sent as tagged serial lines (@shortcut:...) and require a desktop shortcut bridge to become "
                       "real OS keystrokes -- the ESP32 itself only speaks serial, it does not act as a USB keyboard.",
                  style="Dim.TLabel", wraplength=900).grid(row=len(self.button_action_vars) + 1, column=0,
                                                            columnspan=4, sticky="w", pady=(8, 0))

    # -- OLED Designer tab ------------------------------------------------
    def _build_oled_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=0)

        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="n")

        text_frame = ttk.Labelframe(left, text="Text", padding=10)
        text_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(text_frame, text="Title").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(text_frame, textvariable=self.var_oled_title, width=22).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Checkbutton(text_frame, text="Show title", variable=self.var_show_title).grid(row=0, column=2, sticky="w")

        ttk.Label(text_frame, text="Footer").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(text_frame, textvariable=self.var_oled_footer, width=22).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Checkbutton(text_frame, text="Show footer", variable=self.var_show_footer).grid(row=1, column=2, sticky="w")

        ttk.Checkbutton(text_frame, text="Show status/mute indicator", variable=self.var_show_status).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(text_frame, text="Show channel labels", variable=self.var_show_labels).grid(
            row=3, column=0, columnspan=2, sticky="w")

        layout_frame = ttk.Labelframe(left, text="Layout", padding=10)
        layout_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(layout_frame, text="Screen mode").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(layout_frame, textvariable=self.var_layout, state="readonly", width=14,
                     values=config.OLED_LAYOUTS).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(layout_frame, text="Meter style").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(layout_frame, textvariable=self.var_meter_style, state="readonly", width=14,
                     values=config.METER_STYLES).grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(layout_frame, text="Widget layout").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(layout_frame, textvariable=self.var_widget_layout, state="readonly", width=18,
                     values=config.WIDGET_LAYOUTS).grid(row=2, column=1, sticky="w", padx=8)

        toggles_frame = ttk.Labelframe(left, text="Indicators", padding=10)
        toggles_frame.pack(fill="x")
        ttk.Checkbutton(toggles_frame, text="Master widget", variable=self.var_show_master).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(toggles_frame, text="Mute indicator", variable=self.var_show_mute).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(toggles_frame, text="Peak indicator", variable=self.var_show_peak).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(toggles_frame, text="Numeric values", variable=self.var_show_numeric).grid(row=3, column=0, sticky="w")

        # Preview
        preview_frame = ttk.Labelframe(right, text="Live OLED preview (monochrome, to scale)", padding=10)
        preview_frame.pack()
        self.oled_canvas = tk.Canvas(preview_frame, width=128 * 4 + 20, height=64 * 4 + 20,
                                      highlightthickness=0, bd=0)
        self.oled_canvas.pack()
        ttk.Label(preview_frame,
                  text="Shown in strict black/white to match the physical SSD1306 panel.\n"
                       "Desktop accent colors are never used inside the display area.",
                  style="Dim.TLabel", justify="center", wraplength=280).pack(pady=(8, 0))

    # -- Build & Upload tab -----------------------------------------------
    def _build_build_tab(self, parent):
        parent.columnconfigure(0, weight=1)

        arduino_frame = ttk.Labelframe(parent, text="Arduino", padding=12)
        arduino_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(arduino_frame, text="Arduino CLI").grid(row=0, column=0, sticky="w")
        ttk.Entry(arduino_frame, textvariable=self.var_cli_path, width=52).grid(
            row=0, column=1, sticky="we", padx=8)
        ttk.Button(arduino_frame, text="Browse...", command=self._browse_cli_path).grid(row=0, column=2, padx=4)
        ttk.Button(arduino_frame, text="Detect", command=self.action_detect_cli).grid(row=0, column=3, padx=4)
        ttk.Label(arduino_frame, text="COM port").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.com_port_combo = ttk.Combobox(arduino_frame, textvariable=self.var_com_port, width=20, values=[])
        self.com_port_combo.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Button(arduino_frame, text="Refresh", command=self.action_refresh_ports).grid(row=1, column=2, padx=4, pady=(8, 0))
        ttk.Label(arduino_frame, textvariable=self.cli_status_var, style="Dim.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(arduino_frame, text="Sketch").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(arduino_frame, textvariable=self.var_build_target, state="readonly", width=24,
                     values=config.BUILD_TARGETS).grid(row=3, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(arduino_frame, textvariable=self.sketch_path_var, style="Dim.TLabel").grid(
            row=4, column=1, columnspan=3, sticky="w", padx=8, pady=(4, 0))
        actions = ttk.Frame(arduino_frame)
        actions.grid(row=5, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Button(actions, text="Generate & Upload", style="Accent.TButton",
                   command=lambda: self.action_compile_and_upload(regenerate=True)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Upload Existing",
                   command=lambda: self.action_compile_and_upload(regenerate=False)).pack(side="left")
        arduino_frame.columnconfigure(1, weight=1)

        log_frame = ttk.Labelframe(parent, text="Output", padding=10)
        log_frame.pack(fill="both", expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap="word", state="disabled",
                                                    font=("Consolas", 9))
        theme.style_classic_widget(self.log_text, self.palette)
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="Clear log", command=self._clear_log).pack(anchor="e", pady=(6, 0))

    def _build_tests_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        led_frame = ttk.Labelframe(parent, text="LED strip", padding=12)
        led_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(led_frame, text="Choose one LED pattern to test the strip only.", style="Dim.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(led_frame, textvariable=self.var_hardware_test, state="readonly", width=20,
                     values=[f"LED strip - {test}" for test in config.LED_TESTS]).grid(
                         row=1, column=0, sticky="w")
        ttk.Button(led_frame, text="Generate", style="Accent.TButton",
                   command=self.action_generate_led_test).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Button(led_frame, text="Generate & Open", command=self.action_open_led_test).grid(
            row=1, column=2, sticky="w")

        self._build_single_test_section(
            parent, "Potentiometers", "Print each potentiometer's raw ADC value over serial.")
        self._build_single_test_section(
            parent, "Buttons", "Print a message when either button is pressed or released.")
        self._build_single_test_section(
            parent, "OLED display", "Draw a border, test text, and an animated bar on the OLED.")

    def _build_single_test_section(self, parent, test_name, description):
        frame = ttk.Labelframe(parent, text=test_name, padding=12)
        frame.pack(fill="x", pady=(0, 10))
        ttk.Label(frame, text=description, style="Dim.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(frame, text="Generate", command=lambda: self.action_generate_hardware_test(test_name)).grid(
            row=0, column=1, sticky="w", padx=(12, 8))
        ttk.Button(frame, text="Generate & Open",
                   command=lambda: self.action_open_hardware_test(test_name)).grid(row=0, column=2, sticky="w")

    def _build_bottom_bar(self):
        bar = ttk.Frame(self, padding=(14, 8))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.validation_summary_var).pack(side="left")

    def _choose_led_color(self, variable, title):
        _rgb, hex_color = colorchooser.askcolor(color=variable.get(), title=title)
        if hex_color:
            variable.set(hex_color.upper())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def refresh_validation(self):
        cfg = self.collect()
        issues = config.validate_config(cfg)
        self.current_issues = issues

        for field, label in self.hw_issue_labels.items():
            matching = [i for i in issues if i["field"] == field]
            if not matching:
                label.configure(text="")
                continue
            worst = "error" if any(i["level"] == "error" for i in matching) else "warning"
            style = "Error.TLabel" if worst == "error" else "Warning.TLabel"
            label.configure(text=" / ".join(i["message"] for i in matching), style=style)

        n_err = sum(1 for i in issues if i["level"] == "error")
        n_warn = sum(1 for i in issues if i["level"] == "warning")
        if n_err:
            self.validation_summary_var.set(f"\u26A0 {n_err} error(s), {n_warn} warning(s) -- fix errors before uploading.")
        elif n_warn:
            self.validation_summary_var.set(f"\u2713 No errors -- {n_warn} warning(s).")
        else:
            self.validation_summary_var.set("\u2713 Configuration looks good.")

    # ------------------------------------------------------------------
    # OLED live preview
    # ------------------------------------------------------------------
    def redraw_oled_preview(self):
        canvas = getattr(self, "oled_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")

        scale = 4
        w, h = 128, 64
        bezel = 10
        ox, oy = bezel, bezel

        bezel_color = self.var_preview_bg.get().strip() or self.palette["panel"]
        try:
            canvas.configure(bg=bezel_color)
        except tk.TclError:
            bezel_color = self.palette["panel"]
            canvas.configure(bg=bezel_color)

        canvas.create_rectangle(ox - 4, oy - 4, ox + w * scale + 4, oy + h * scale + 4,
                                 fill=bezel_color, outline="#000000")
        canvas.create_rectangle(ox, oy, ox + w * scale, oy + h * scale, fill="#000000", outline="#000000")

        def px(x, y, pw, ph):
            if pw <= 0 or ph <= 0:
                return
            canvas.create_rectangle(ox + x * scale, oy + y * scale,
                                     ox + (x + pw) * scale, oy + (y + ph) * scale,
                                     fill="#FFFFFF", outline="#FFFFFF")

        def frame(x, y, fw, fh):
            canvas.create_rectangle(ox + x * scale, oy + y * scale,
                                     ox + (x + fw) * scale, oy + (y + fh) * scale,
                                     outline="#FFFFFF")

        def text(x, y, s, small=False, right_edge=w):
            if not s:
                return
            glyph = _OLED_FONT_3X5
            glyph_width = 3
            glyph_height = 5
            advance = glyph_width + 1
            x = max(0, min(int(x), w - 1))
            y = max(0, min(int(y), h - glyph_height))
            right_edge = max(x, min(int(right_edge), w))
            max_chars = max(0, (right_edge - x) // advance)
            clipped = str(s).upper()[:max_chars]
            for char_index, char in enumerate(clipped):
                glyph_rows = glyph.get(char, glyph[" "])
                for row, bits in enumerate(glyph_rows):
                    for col, bit in enumerate(bits[:glyph_width]):
                        if bit == "1" and x + char_index * advance + col < right_edge and y + row < h:
                            px(x + char_index * advance + col, y + row, 1, 1)

        title = self.var_oled_title.get()
        footer = self.var_oled_footer.get()
        show_title = self.var_show_title.get()
        show_footer = self.var_show_footer.get()
        show_status = self.var_show_status.get()
        show_labels = self.var_show_labels.get()
        show_numeric = self.var_show_numeric.get()
        show_master = self.var_show_master.get()
        layout = self.var_layout.get()
        meter_style = self.var_meter_style.get()
        widget_layout = self.var_widget_layout.get()
        show_master_widget = show_master or widget_layout == "Master + channels"

        n_ch = len(self.channel_vars)
        if widget_layout == "Three channels":
            max_shown = min(3, n_ch)
        elif widget_layout == "Status only":
            max_shown = 0
        else:
            max_shown = n_ch

        demo_pct = [30, 55, 82, 45, 68, 20, 90][:n_ch]

        shown = []
        for i, vars_ in enumerate(self.channel_vars):
            if i >= max_shown:
                continue
            if not vars_["visible"].get() or vars_["style"].get() == "Hidden":
                continue
            shown.append(i)

        if show_title and title:
            text(2, 1, title, right_edge=94 if show_status else w)
            canvas.create_line(ox, oy + 11 * scale, ox + w * scale, oy + 11 * scale, fill="#FFFFFF")
        if show_footer and footer:
            text(2, 57, footer, small=True)
        if show_status:
            text(96, 1, "MUTE", small=True)

        top = 14 if show_title else 2
        bottom = 60 if show_footer else 63

        if widget_layout == "Status only" or layout == "Status":
            text(4, top + 2, "Status: OK")
            self._demo_canvas = canvas
            return

        if layout == "Minimal":
            y = top
            for idx, i in enumerate(shown):
                pct = demo_pct[i % len(demo_pct)]
                label = self.channel_vars[i]["label"].get() or f"CH{i + 1}"
                s = f"{label} {pct:3d}%" if show_labels else f"{pct:3d}%"
                text(2, y, s, small=True)
                y += 10
                if y > bottom - 2:
                    break
            return

        if layout == "Numeric":
            y = top
            for i in shown:
                pct = demo_pct[i % len(demo_pct)]
                val = int(pct * 1023 / 100)
                label = self.channel_vars[i]["label"].get() or f"CH{i + 1}"
                s = f"{label:<5}{val:4d}" if show_labels else f"{val:4d}"
                text(2, y, s, small=True)
                y += 10
                if y > bottom - 2:
                    break
            return

        # Meters layout (default)
        if meter_style == "Horizontal":
            rows = max(1, len(shown))
            usable = bottom - top
            row_h = max(8, usable // rows)
            y = top
            for i in shown:
                pct = demo_pct[i % len(demo_pct)]
                label_w = 20 if show_labels else 0
                bar_max = 80
                bar_w = int(pct * bar_max / 100)
                if show_labels:
                    label = self.channel_vars[i]["label"].get() or f"CH{i + 1}"
                    text(2, y, label, small=True)
                frame(2 + label_w, y, bar_max, row_h - 2)
                px(2 + label_w, y, bar_w, row_h - 2)
                if show_numeric:
                    val = int(pct * 1023 / 100)
                    text(2 + label_w + bar_max + 3, y, str(val), small=True)
                y += row_h
            return

        # Vertical bars
        label_h = 8 if show_labels else 0
        bar_top = top
        bar_bottom = bottom - label_h
        bar_max_h = max(4, bar_bottom - bar_top)

        cols = len(shown) + (1 if show_master_widget else 0)
        cols = max(cols, 1)
        col_w = 126 // cols
        x = 2

        if show_master_widget:
            avg = int(sum(demo_pct[i % len(demo_pct)] for i in shown) / len(shown)) if shown else 0
            bar_h = int(avg * bar_max_h / 100)
            frame(x, bar_bottom - bar_max_h, col_w - 3, bar_max_h)
            px(x, bar_bottom - bar_h, col_w - 3, bar_h)
            if show_labels:
                text(x, bottom, "MSTR", small=True)
            x += col_w

        for i in shown:
            pct = demo_pct[i % len(demo_pct)]
            bar_h = int(pct * bar_max_h / 100)
            frame(x, bar_bottom - bar_max_h, col_w - 3, bar_max_h)
            px(x, bar_bottom - bar_h, col_w - 3, bar_h)
            if show_labels:
                label = self.channel_vars[i]["label"].get() or f"CH{i + 1}"
                text(x, bottom, label, small=True)
            if show_numeric:
                val = int(pct * 1023 / 100)
                text(x, bar_bottom - bar_max_h - 8, str(val), small=True)
            x += col_w

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------
    def action_save(self):
        cfg = self.collect()
        config.save_config(self.config_path, cfg)
        self.cfg = cfg
        self.top_status_var.set(f"Saved to {self.config_path}")

    def action_load(self):
        path = filedialog.askopenfilename(
            title="Load configuration",
            initialdir=_app_dir(),
            initialfile="mixer_config.json",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        cfg = config.load_config(path)
        self.config_path = path
        self.cfg = cfg
        self._apply_cfg_to_vars(cfg)
        self.top_status_var.set(f"Loaded {path}")

    # ------------------------------------------------------------------
    # Arduino CLI / COM ports
    # ------------------------------------------------------------------
    def _browse_cli_path(self):
        filetypes = [("arduino-cli", "arduino-cli.exe" if arduino_tools.IS_WINDOWS else "arduino-cli"),
                     ("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select arduino-cli executable", filetypes=filetypes)
        if path:
            self.var_cli_path.set(path)

    def action_detect_cli(self):
        found = arduino_tools.find_arduino_cli(self.var_cli_path.get().strip())
        if found:
            self.var_cli_path.set(found)
            version = arduino_tools.get_cli_version(found)
            self.cli_status_var.set(f"Found: {found}" + (f" ({version})" if version else ""))
        else:
            self.cli_status_var.set("Not found on PATH or in common locations. Use Browse to select arduino-cli.exe.")

    def action_refresh_ports(self):
        ports = arduino_tools.list_com_ports(self.var_cli_path.get().strip())
        self.com_port_combo.configure(values=ports)
        if ports and self.var_com_port.get() not in ports:
            self.var_com_port.set(ports[0])
        if not ports:
            self._log("No COM ports detected. Connect the ESP32-C3 and click Refresh again.")

    def action_quick_setup(self):
        self.action_detect_cli()
        if "Not found" in self.cli_status_var.get():
            self._browse_cli_path()
            if self.var_cli_path.get():
                self.action_detect_cli()
        self.action_refresh_ports()
        self.top_status_var.set("Quick setup complete.")

    # ------------------------------------------------------------------
    # Sketch generation / open / build / upload
    # ------------------------------------------------------------------
    def _sketch_dir(self) -> str:
        # arduino-cli and the Arduino IDE both require the sketch folder
        # name to match the main .ino file name, so the sketch lives in
        # arduino_build/deej_esp32/deej_esp32.ino.
        return os.path.join(_app_dir(), "arduino_build", "deej_esp32")

    def _sketch_path(self) -> str:
        return os.path.join(self._sketch_dir(), "deej_esp32.ino")

    def _generate_sketch(self, cfg: dict) -> str:
        config.save_config(self.config_path, cfg)
        sketch = firmware_generator.generate_firmware(cfg)
        sketch_dir = self._sketch_dir()
        os.makedirs(sketch_dir, exist_ok=True)
        path = self._sketch_path()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(sketch)
        self.sketch_path_var.set(path)
        self._set_editor_text(sketch)
        return path

    def _set_editor_text(self, text):
        editor = getattr(self, "code_editor", None)
        if editor is None:
            return
        editor.delete("1.0", "end")
        editor.insert("1.0", text)

    def action_load_editor(self):
        path = self._sketch_path()
        if not os.path.isfile(path):
            messagebox.showerror("No generated sketch", "Generate the main sketch before loading it into the editor.")
            return
        with open(path, "r", encoding="utf-8") as fh:
            self._set_editor_text(fh.read())
        self.top_status_var.set("Generated sketch loaded into editor.")

    def action_save_editor(self):
        editor = getattr(self, "code_editor", None)
        if editor is None:
            return None
        path = self._sketch_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(editor.get("1.0", "end-1c"))
        self.sketch_path_var.set(path)
        self.top_status_var.set("Edited sketch saved.")
        self._log(f"[ok] Saved edited sketch {path}")
        return path

    def action_upload_editor(self):
        if not self.action_save_editor():
            return
        self.action_compile_and_upload(regenerate=False)

    def _led_test_path(self) -> str:
        test_dir = os.path.join(_app_dir(), "arduino_build", "led_strip_test")
        os.makedirs(test_dir, exist_ok=True)
        return os.path.join(test_dir, "led_strip_test.ino")

    def _generate_hardware_test_sketch(self, selected=None) -> str | None:
        cfg = self.collect()
        selected = selected or self.var_hardware_test.get()
        if selected.startswith("LED strip - "):
            cfg["hardware"]["led_strip"]["enabled"] = True
        issues = config.validate_config(cfg)
        if self._blocking_errors_dialog(issues):
            return None
        path = self._led_test_path()
        if selected.startswith("LED strip - "):
            sketch = firmware_generator.generate_led_test_firmware(cfg, selected.removeprefix("LED strip - "))
        else:
            sketch = firmware_generator.generate_hardware_test_firmware(cfg, selected)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(sketch)
        return path

    def _blocking_errors_dialog(self, issues) -> bool:
        """Show an error dialog if `issues` contains any errors. Returns
        True if generation/upload should be aborted."""
        errors = [i for i in issues if i["level"] == "error"]
        if not errors:
            return False
        text = "\n".join(f"- {i['field']}: {i['message']}" for i in errors)
        messagebox.showerror("Fix these errors first", text)
        return True

    def action_generate_only(self):
        cfg = self.collect()
        issues = config.validate_config(cfg)
        if self._blocking_errors_dialog(issues):
            return
        path = self._generate_sketch(cfg)
        self._log(f"[ok] Generated {path}")
        self.top_status_var.set("Sketch generated.")

    def action_generate_led_test(self):
        path = self._generate_hardware_test_sketch()
        if path:
            self._log(f"[ok] Generated LED test sketch {path}")
            self.top_status_var.set("LED test sketch generated.")

    def action_open_led_test(self):
        path = self._generate_hardware_test_sketch()
        if not path:
            return
        result = arduino_tools.open_path_with_default_app(path)
        if result.ok:
            self._log(f"[ok] Opened LED test sketch {path}")
            self.top_status_var.set("LED test sketch opened.")
        else:
            messagebox.showerror("Could not open LED test sketch", result.message)

    def action_generate_hardware_test(self, test_name):
        path = self._generate_hardware_test_sketch(test_name)
        if path:
            self._log(f"[ok] Generated {test_name} test sketch {path}")
            self.top_status_var.set(f"{test_name} test sketch generated.")

    def action_open_hardware_test(self, test_name):
        path = self._generate_hardware_test_sketch(test_name)
        if not path:
            return
        result = arduino_tools.open_path_with_default_app(path)
        if result.ok:
            self._log(f"[ok] Opened {test_name} test sketch {path}")
            self.top_status_var.set(f"{test_name} test sketch opened.")
        else:
            messagebox.showerror(f"Could not open {test_name} test sketch", result.message)

    def action_open_sketch(self):
        cfg = self.collect()
        issues = config.validate_config(cfg)
        errors = [i for i in issues if i["level"] == "error"]
        if errors:
            text = "\n".join(f"- {i['field']}: {i['message']}" for i in errors)
            if not messagebox.askyesno("Config has errors",
                                        f"{text}\n\nOpen the sketch anyway?"):
                return
        path = self._generate_sketch(cfg)
        result = arduino_tools.open_path_with_default_app(path)
        if result.ok:
            self._log(f"[ok] Opened {path}")
        else:
            messagebox.showerror("Could not open sketch", result.message)

    def action_compile_and_upload(self, regenerate: bool):
        cfg = self.collect()
        issues = config.validate_config(cfg)
        if self._blocking_errors_dialog(issues):
            return

        cli = self.var_cli_path.get().strip()
        if not cli or not os.path.isfile(cli):
            messagebox.showerror("Arduino CLI required",
                                  "Set a valid Arduino CLI path (Quick Setup or Browse) before uploading.")
            return

        port = self.var_com_port.get().strip()
        if not port:
            messagebox.showerror("COM port required", "Select a COM port before uploading.")
            return

        fqbn = cfg["hardware"]["board_fqbn"]

        self.action_stop_serial_monitor()

        target = self.var_build_target.get()
        test_names = {
            "LED strip test": "LED strip - Full sequence",
            "Potentiometers test": "Potentiometers",
            "Buttons test": "Buttons",
            "OLED display test": "OLED display",
        }
        if target == "Main mixer":
            target_path = self._sketch_path()
            target_dir = self._sketch_dir()
            if regenerate:
                self._generate_sketch(cfg)
            elif not os.path.isfile(target_path):
                messagebox.showerror("No sketch found", "Generate the main mixer sketch before Upload Existing.")
                return
        else:
            if regenerate:
                target_path = self._generate_hardware_test_sketch(test_names[target])
                if not target_path:
                    return
            else:
                target_path = self._led_test_path()
                if not os.path.isfile(target_path):
                    messagebox.showerror("No test sketch found", "Generate this hardware test before Upload Existing.")
                    return
            target_dir = os.path.dirname(target_path)

        self.top_status_var.set(f"Compiling & uploading {target}...")
        self._log(f"--- Compile & upload started for {target} ({'regenerated' if regenerate else 'existing'} sketch) ---")

        def worker():
            result = arduino_tools.compile_and_upload(cli, fqbn, port, target_dir, self._log)
            self._set_status_async("Upload succeeded." if result.ok else "Upload failed -- see log.")
            self._log("[done] SUCCESS" if result.ok else f"[done] FAILED (exit code {result.exit_code})")

        threading.Thread(target=worker, daemon=True).start()

    def action_start_serial_monitor(self):
        if self.serial_process is not None and self.serial_process.poll() is None:
            return
        cli = self.var_cli_path.get().strip()
        port = self.var_com_port.get().strip()
        if not cli or not os.path.isfile(cli):
            messagebox.showerror("Arduino CLI required", "Set a valid Arduino CLI path before starting the monitor.")
            return
        if not port:
            messagebox.showerror("COM port required", "Select a COM port before starting the monitor.")
            return
        try:
            process = arduino_tools.start_serial_monitor(cli, port, self._to_int(self.var_baud.get(), 115200))
        except OSError as exc:
            messagebox.showerror("Could not start serial monitor", str(exc))
            return
        self.serial_process = process
        self.serial_status_var.set(f"Monitoring {port}")
        threading.Thread(target=self._read_serial_monitor, args=(process,), daemon=True).start()

    def _read_serial_monitor(self, process):
        if process.stdout is not None:
            for line in process.stdout:
                self.log_queue.put(("serial", line.rstrip("\r\n")))
        process.wait()
        if self.serial_process is process:
            self.log_queue.put(("serial_status", "Stopped"))

    def action_stop_serial_monitor(self):
        process = self.serial_process
        if process is None or process.poll() is not None:
            self.serial_status_var.set("Stopped")
            return
        process.terminate()
        self.serial_process = None
        self.serial_status_var.set("Stopped")

    def _clear_serial_log(self):
        self.serial_text.configure(state="normal")
        self.serial_text.delete("1.0", "end")
        self.serial_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Appearance dialog (desktop-only; never edits generated firmware)
    # ------------------------------------------------------------------
    def open_appearance_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("Appearance")
        dialog.configure(bg=self.palette["bg"])
        dialog.geometry("380x220")
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="These settings only affect this desktop app's look;\n"
                              "they never change the monochrome OLED firmware output.",
                  style="Dim.TLabel", justify="left").pack(anchor="w", pady=(0, 12))

        def pick_accent():
            _rgb, hex_color = colorchooser.askcolor(color=self.var_accent.get(), title="Accent color")
            if hex_color:
                self.var_accent.set(hex_color)
                self.palette = theme.apply_dark_theme(self, accent=hex_color)

        def pick_preview_bg():
            _rgb, hex_color = colorchooser.askcolor(color=self.var_preview_bg.get(), title="Preview bezel color")
            if hex_color:
                self.var_preview_bg.set(hex_color)
                self.redraw_oled_preview()

        row1 = ttk.Frame(frame)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="Accent color").pack(side="left")
        ttk.Button(row1, text="Choose...", command=pick_accent).pack(side="right")

        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="OLED preview bezel color").pack(side="left")
        ttk.Button(row2, text="Choose...", command=pick_preview_bg).pack(side="right")

        def reset_defaults():
            defaults = config.default_config()["appearance"]
            self.var_accent.set(defaults["accent"])
            self.var_preview_bg.set(defaults["preview_bg"])
            self.palette = theme.apply_dark_theme(self, accent=defaults["accent"])
            self.redraw_oled_preview()

        ttk.Button(frame, text="Reset to defaults", command=reset_defaults).pack(anchor="w", pady=(12, 0))
        ttk.Button(frame, text="Close", command=dialog.destroy).pack(anchor="e", pady=(16, 0))

    # ------------------------------------------------------------------
    # Logging / background-thread plumbing
    # ------------------------------------------------------------------
    def _log(self, line: str):
        self.log_queue.put(("line", line))

    def _set_status_async(self, text: str):
        self.log_queue.put(("status", text))

    def _drain_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "line":
                    self._append_log(payload)
                elif kind == "serial":
                    self._append_serial_line(payload)
                elif kind == "serial_status":
                    self.serial_status_var.set(payload)
                elif kind == "status":
                    self.top_status_var.set(payload)
        except queue.Empty:
            pass
        self.after(80, self._drain_log_queue)

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_serial_line(self, line: str):
        self.serial_text.configure(state="normal")
        self.serial_text.insert("end", line + "\n")
        self.serial_text.see("end")
        self.serial_text.configure(state="disabled")

    def _on_close(self):
        self.action_stop_serial_monitor()
        self.destroy()


def main():
    app = MixerConfiguratorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
