"""Palette for the tray control window."""

PALETTE = {
    "ink": "#e6edf7",
    "night": "#0f1725",
    "surface": "#182436",
    "line": "#354a64",
    "quiet": "#aabbd0",
    "signal": "#7ce5dd",
    "fault": "#ffb4a5",
}


def palette_css():
    return "".join(f"@define-color relay_{name} {color};\n" for name, color in PALETTE.items())
