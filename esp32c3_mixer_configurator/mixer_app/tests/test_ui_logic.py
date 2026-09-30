"""Headless smoke test of desktop_app.py logic using a mocked tkinter layer.

Verifies (without a display): the app constructs, collect() works without the
Appearance dialog ever being opened, validation reacts to field changes, the
OLED preview redraws when designer settings change, and loading old configs
never crashes.
"""
import os, sys, types
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class Var:
    def __init__(self, master=None, value=None):
        self._v = value
        self._cbs = []
    def get(self): return self._v
    def set(self, v):
        self._v = v
        for cb in list(self._cbs):
            cb()
    def trace_add(self, mode, cb): self._cbs.append(lambda *a, cb=cb: cb())


class FakeTk:
    def __init__(self, *a, **k): pass
    def __getattr__(self, name): return MagicMock()


class FakeCanvas(MagicMock):
    pass


tk = types.ModuleType("tkinter")
tk.Tk = FakeTk
tk.StringVar = Var
tk.BooleanVar = Var
tk.Toplevel = MagicMock
tk.TclError = Exception
tk.Canvas = MagicMock(side_effect=lambda *a, **k: FakeCanvas())
ttk = MagicMock()
for name in ("filedialog", "messagebox", "colorchooser", "scrolledtext"):
    setattr(tk, name, MagicMock())
tk.ttk = ttk
sys.modules["tkinter"] = tk
sys.modules["tkinter.ttk"] = ttk
for name in ("filedialog", "messagebox", "colorchooser", "scrolledtext"):
    sys.modules[f"tkinter.{name}"] = getattr(tk, name)

import desktop_app, config  # noqa: E402

app = desktop_app.MixerConfiguratorApp()
fails = []
def check(n, c):
    print(("[OK] " if c else "[FAIL] ") + n)
    if not c: fails.append(n)

cfg = app.collect()
check("collect() works without Appearance dialog", cfg["appearance"]["accent"] and cfg["appearance"]["preview_bg"])
check("collected default config has no errors", not config.has_errors(config.validate_config(cfg)))

# duplicate GPIO -> error reported
app.var_slider_pins[1].set("0")
check("duplicate GPIO detected", any("more than one" in i["message"] for i in app.current_issues))
app.var_slider_pins[1].set("1")
check("duplicate cleared after fix", not config.has_errors(app.current_issues))

# invalid data -> clear errors, no crash
app.var_slider_pins[0].set("abc")
check("invalid pin text flagged, no crash", config.has_errors(app.current_issues))
app.var_slider_pins[0].set("0")
app.var_oled_addr.set("3C")
check("invalid OLED address flagged", any(i["field"] == "oled_address" for i in app.current_issues))
app.var_oled_addr.set("0x3C")
app.var_fqbn.set("")
check("missing FQBN flagged", any(i["field"] == "board_fqbn" for i in app.current_issues))
app.var_fqbn.set("esp32:esp32:esp32c3")

# preview redraws on designer changes
canvas = app.oled_canvas
def redraws(action):
    canvas.reset_mock()
    action()
    return canvas.delete.called
check("preview redraws: layout", redraws(lambda: app.var_layout.set("Numeric")))
check("preview redraws: widget layout", redraws(lambda: app.var_widget_layout.set("Three channels")))
check("preview redraws: channel label", redraws(lambda: app.channel_vars[0]["label"].set("MIC")))
check("preview redraws: visibility", redraws(lambda: app.channel_vars[1]["visible"].set(False)))
check("preview redraws: style", redraws(lambda: app.channel_vars[2]["style"].set("Hidden")))
check("preview redraws: title checkbox", redraws(lambda: app.var_show_title.set(False)))
check("preview redraws: numeric option", redraws(lambda: app.var_show_numeric.set(False)))

# custom shortcut validation
app.button_action_vars[0]["action"].set("Custom shortcut")
app.button_action_vars[0]["custom"].set("")
check("empty custom shortcut flagged", any("custom" in i["field"] for i in app.current_issues))
app.button_action_vars[0]["custom"].set("CTRL+ALT+M")
check("custom shortcut accepted", not config.has_errors(app.current_issues))

# profile switch fills SDA/SCL
app.var_profile.set("SuperMini (SDA=6 / SCL=7)")
check("profile fills SDA/SCL", app.var_sda.get() == "6" and app.var_scl.get() == "7")

# load old partial config into vars
app._apply_cfg_to_vars(config._deep_merge(config.default_config(), {"hardware": {"slider_pins": [0,1,2,3,4]}}))
check("apply old config does not crash", True)

# generate sketch end-to-end through the app helper
import tempfile
app._sketch_dir = lambda: os.path.join(tempfile.mkdtemp(), "deej_esp32")
app._sketch_path = lambda: os.path.join(app._sketch_dir.__call__(), "x.ino")
app.config_path = os.path.join(tempfile.mkdtemp(), "mixer_config.json")
sd = tempfile.mkdtemp(); app._sketch_dir = lambda: sd
app._sketch_path = lambda: os.path.join(sd, "deej_esp32.ino")
p = app._generate_sketch(app.collect())
check("generated sketch path exists", os.path.isfile(p) and os.path.isfile(app.config_path))

print("\nFAILED: %s" % fails if fails else "\nAll UI-logic checks passed.")
sys.exit(1 if fails else 0)
