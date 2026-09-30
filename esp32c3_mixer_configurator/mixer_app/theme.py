"""Dark theme for the Tkinter/ttk desktop app.

Applies a consistent dark palette across ttk widgets (Notebook, Entry,
Combobox, Button, Checkbutton, Spinbox, Labelframe, Scrollbar) and exposes
the palette so classic tk widgets (Canvas, Text, plain Frame/Label) can be
colored to match -- ttk's theme engine does not touch those.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PALETTE = {
    "bg": "#111318",
    "bg_alt": "#1A1E25",
    "panel": "#222730",
    "field_bg": "#2A303A",
    "border": "#3B4450",
    "fg": "#F2F4F7",
    "fg_dim": "#AAB3BF",
    "accent": "#5EA2FF",
    "error": "#FF7777",
    "warning": "#F3C45B",
    "ok": "#6DDB9A",
    "select_bg": "#304766",
}


def apply_dark_theme(root: tk.Tk, accent: str | None = None) -> dict:
    """Configure ttk.Style for a dark theme. Returns the palette dict in use
    (with `accent` overridden if provided) so callers can reuse the colors
    for classic tk widgets."""
    palette = dict(PALETTE)
    if accent:
        palette["accent"] = accent

    root.configure(bg=palette["bg"])

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=palette["bg"], foreground=palette["fg"],
                     fieldbackground=palette["field_bg"], bordercolor=palette["border"],
                     darkcolor=palette["bg"], lightcolor=palette["bg"],
                     troughcolor=palette["bg_alt"], relief="flat", font=("Segoe UI", 10))

    style.configure("TFrame", background=palette["bg"])
    style.configure("Panel.TFrame", background=palette["panel"])

    style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
    style.configure("Dim.TLabel", background=palette["bg"], foreground=palette["fg_dim"])
    style.configure("Error.TLabel", background=palette["bg"], foreground=palette["error"], font=("Segoe UI", 8))
    style.configure("Warning.TLabel", background=palette["bg"], foreground=palette["warning"], font=("Segoe UI", 8))
    style.configure("Heading.TLabel", background=palette["bg"], foreground=palette["fg"],
                    font=("Segoe UI", 14, "bold"))
    style.configure("Subtitle.TLabel", background=palette["bg"], foreground=palette["fg_dim"],
                    font=("Segoe UI", 9))
    style.configure("Status.TLabel", background=palette["panel"], foreground=palette["fg_dim"],
                    padding=(8, 4), font=("Segoe UI", 9))

    style.configure("TLabelframe", background=palette["bg"], foreground=palette["fg"],
                    bordercolor=palette["border"], padding=8)
    style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["fg_dim"], font=("Segoe UI", 9, "bold"))

    style.configure("TButton", background=palette["panel"], foreground=palette["fg"],
                     bordercolor=palette["border"], padding=(12, 7), font=("Segoe UI", 9, "bold"))
    style.map("TButton",
              background=[("active", palette["select_bg"]), ("disabled", palette["bg_alt"])],
              foreground=[("disabled", palette["fg_dim"])])

    style.configure("Accent.TButton", background=palette["accent"], foreground="#101318",
                    padding=(14, 8), font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", "#79B4FF"), ("disabled", palette["bg_alt"])],
              foreground=[("disabled", palette["fg_dim"])])

    style.configure("TEntry", fieldbackground=palette["field_bg"], foreground=palette["fg"],
                     insertcolor=palette["fg"], bordercolor=palette["border"], padding=(6, 4))
    style.map("TEntry", fieldbackground=[("disabled", palette["bg_alt"])])

    style.configure("TSpinbox", fieldbackground=palette["field_bg"], foreground=palette["fg"],
                     background=palette["panel"], arrowcolor=palette["fg"], bordercolor=palette["border"])

    style.configure("TCombobox", fieldbackground=palette["field_bg"], background=palette["panel"],
                     foreground=palette["fg"], arrowcolor=palette["fg"], bordercolor=palette["border"],
                     padding=(6, 4))
    style.map("TCombobox",
              fieldbackground=[("readonly", palette["field_bg"]), ("disabled", palette["bg_alt"])],
              foreground=[("readonly", palette["fg"]), ("disabled", palette["fg_dim"])],
              selectbackground=[("readonly", palette["field_bg"])],
              selectforeground=[("readonly", palette["fg"])])
    root.option_add("*TCombobox*Listbox.background", palette["field_bg"])
    root.option_add("*TCombobox*Listbox.foreground", palette["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", palette["select_bg"])
    root.option_add("*TCombobox*Listbox.selectForeground", palette["fg"])

    style.configure("TCheckbutton", background=palette["bg"], foreground=palette["fg"])
    style.map("TCheckbutton", background=[("active", palette["bg"])])

    style.configure("TNotebook", background=palette["bg"], bordercolor=palette["border"])
    style.configure("TNotebook.Tab", background=palette["bg_alt"], foreground=palette["fg"],
                     padding=(16, 9), font=("Segoe UI", 9, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", palette["panel"])],
              foreground=[("selected", palette["accent"]), ("!selected", palette["fg_dim"])])

    style.configure("Vertical.TScrollbar", background=palette["panel"], troughcolor=palette["bg"],
                     bordercolor=palette["border"], arrowcolor=palette["fg"])
    style.configure("Horizontal.TScrollbar", background=palette["panel"], troughcolor=palette["bg"],
                     bordercolor=palette["border"], arrowcolor=palette["fg"])

    style.configure("TSeparator", background=palette["border"])

    return palette


def style_classic_widget(widget, palette: dict, **overrides):
    """Apply palette colors to a classic (non-ttk) tk widget, e.g. Text or
    Canvas, which ttk's theme engine cannot reach."""
    opts = dict(
        bg=palette["field_bg"], fg=palette["fg"],
        insertbackground=palette["fg"], highlightthickness=1,
        highlightbackground=palette["border"], highlightcolor=palette["accent"],
        selectbackground=palette["select_bg"], selectforeground=palette["fg"],
        relief="flat", borderwidth=0,
    )
    opts.update(overrides)
    widget.configure(**opts)
