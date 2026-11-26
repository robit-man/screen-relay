#!/usr/bin/python3
"""Screen Relay desktop entry point."""

import logging
import sys

try:
    import gi
    import pychromecast  # noqa: F401

    for namespace, version in (
        ("Gtk", "3.0"), ("Gdk", "3.0"), ("Pango", "1.0"),
        ("AyatanaAppIndicator3", "0.1"), ("Gst", "1.0"),
    ):
        gi.require_version(namespace, version)
except (ImportError, ValueError) as error:
    sys.exit(f"Screen Relay could not load its dependencies: {error}\nRun install.sh or use the project virtual environment.")

from gi.repository import GLib

from screenrelay.app import RelayApp

GLib.set_application_name("Screen Relay")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
sys.exit(RelayApp().run(sys.argv))
