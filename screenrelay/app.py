"""GTK controls and tray menu for selecting a wireless display."""

import asyncio
import logging
import os
import shutil
import signal
import threading
from pathlib import Path

from gi.repository import AyatanaAppIndicator3 as Indicator
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gst, Gtk, Pango

from .airplay import AirPlaySession, executable
from .cast import CastSession
from .devices import discover
from .media import CastStream, missing_plugins
from .network import playback_monitor
from .portal import ScreenPortal
from .theme import palette_css

APP_ID = "local.screenrelay.ScreenRelay"
ASSETS = Path(__file__).parent
ICONS = ASSETS / "icons"
LOG = logging.getLogger(__name__)


def styled(widget, *classes):
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def label(text, *classes):
    widget = styled(Gtk.Label(label=text, xalign=0), *classes)
    widget.set_line_wrap(True)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


class RelayApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window = None
        self.portal = None
        self.stream = None
        self.session = None
        self.target = None
        self.devices = []
        self.scanning = False
        self.scan_error = False
        self.phase = "idle"
        self.generation = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        Gst.init(None)
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.quit_from_signal)
        self.setup_window()
        self.setup_tray()
        self.set_phase("idle")
        GLib.timeout_add_seconds(20, self.background_scan)

    def quit_from_signal(self):
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_activate(self):
        self.present()
        self.refresh()

    def do_shutdown(self):
        self.release_cast(shutdown=True)
        Gtk.Application.do_shutdown(self)

    def setup_window(self):
        theme = Gtk.CssProvider()
        theme.load_from_data((palette_css() + (ASSETS / "desktop.css").read_text()).encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), theme, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        Gtk.IconTheme.get_default().append_search_path(str(ICONS))

        self.window = styled(Gtk.ApplicationWindow(application=self), "relay-window")
        self.window.set_title("Screen Relay")
        self.window.set_default_size(500, 445)
        self.window.set_size_request(340, 370)
        self.window.connect("delete-event", lambda window, _event: window.hide_on_delete())

        titlebar = styled(Gtk.HeaderBar(title="Screen Relay", show_close_button=True), "relay-titlebar")
        logo = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(ICONS / "screen-relay.png"), 34, 34, True)
        titlebar.pack_start(Gtk.Image.new_from_pixbuf(logo))
        self.window.set_titlebar(titlebar)
        self.window.set_icon_from_file(str(ICONS / "screen-relay.png"))

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.window.add(scroller)
        body = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16), "relay-body")
        scroller.add(body)

        top = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5), "status-card")
        self.flag = label("", "status-flag")
        self.heading = label("", "status-heading")
        self.message = label("", "support")
        top.pack_start(self.flag, False, False, 0)
        top.pack_start(self.heading, False, False, 0)
        top.pack_start(self.message, False, False, 0)
        body.pack_start(top, False, False, 0)

        self.stack = Gtk.Stack(vhomogeneous=False)
        self.stack.add_named(self.device_panel(), "idle")
        self.stack.add_named(self.cast_panel(), "casting")
        body.pack_start(self.stack, False, False, 0)

        self.feedback = label("", "support", "feedback")
        self.feedback.set_size_request(-1, 22)
        body.pack_start(self.feedback, False, False, 0)
        self.window.get_child().show_all()
        titlebar.show_all()

    def device_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        row = Gtk.Box(spacing=12)
        row.pack_start(label("Nearby displays", "form-title"), True, True, 0)
        self.refresh_button = self.action("Refresh", self.refresh)
        row.pack_end(self.refresh_button, False, False, 0)
        panel.pack_start(row, False, False, 0)
        self.device_list = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), "device-list")
        panel.pack_start(self.device_list, False, False, 0)

        audio_row = styled(Gtk.Box(spacing=12), "audio-row")
        audio_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        audio_copy.pack_start(label("Desktop audio", "form-title"), False, False, 0)
        audio_copy.pack_start(label("Include sound playing on this computer.", "support"), False, False, 0)
        self.audio = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
        self.audio.get_accessible().set_name("Desktop audio")
        audio_row.pack_start(audio_copy, True, True, 0)
        audio_row.pack_end(self.audio, False, False, 0)
        panel.pack_start(audio_row, False, False, 0)
        if shutil.which("gnome-network-displays"):
            panel.pack_start(self.action("Open Miracast picker", self.open_miracast), False, False, 0)
        return panel

    def cast_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.progress = Gtk.Spinner(halign=Gtk.Align.CENTER)
        self.progress.set_size_request(-1, 34)
        panel.pack_start(self.progress, False, False, 5)
        self.pairing = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7), "audio-row")
        self.pair_label = label("AirPlay code", "form-title")
        self.code = Gtk.Entry(visibility=False, max_length=128)
        self.code.get_accessible().set_name("AirPlay code")
        self.code.connect("activate", lambda _entry: self.submit_code())
        self.code.connect("changed", lambda entry: self.pair_button.set_sensitive(bool(entry.get_text())))
        self.pair_button = self.action("Connect", self.submit_code, "primary")
        self.pairing.pack_start(self.pair_label, False, False, 0)
        self.pairing.pack_start(self.code, False, False, 0)
        self.pairing.pack_start(self.pair_button, False, False, 0)
        panel.pack_start(self.pairing, False, False, 0)
        self.stop_button = self.action("Cancel cast", self.stop, "stop")
        panel.pack_start(self.stop_button, False, False, 0)
        return panel

    def action(self, title, callback, *classes):
        button = styled(Gtk.Button(label=title) if title else Gtk.Button(), "relay-action", *classes)
        button.connect("clicked", lambda _button: callback())
        return button

    def setup_tray(self):
        self.indicator = Indicator.Indicator.new(
            "screen-relay", "screen-relay-idle", Indicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(str(ICONS))
        self.indicator.set_title("Screen Relay")
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.menu = Gtk.Menu()
        self.menu_item(self.menu, "Open Screen Relay", self.present)
        self.tray_targets = Gtk.MenuItem(label="Cast to a display")
        self.target_menu = Gtk.Menu()
        self.tray_targets.set_submenu(self.target_menu)
        self.menu.append(self.tray_targets)
        self.tray_stop = self.menu_item(self.menu, "Stop casting", self.stop)
        self.menu_item(self.menu, "Refresh displays", self.refresh)
        if shutil.which("gnome-network-displays"):
            self.menu_item(self.menu, "Open Miracast picker", self.open_miracast)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.menu_item(self.menu, "Quit Screen Relay", self.quit)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def menu_item(self, menu, title, callback):
        item = Gtk.MenuItem(label=title)
        item.connect("activate", lambda _item: callback())
        menu.append(item)
        return item

    def present(self):
        self.window.present()

    def open_miracast(self):
        if self.phase != "idle":
            self.feedback.set_text("Stop casting before opening the Miracast picker.")
            self.present()
            return
        try:
            Gio.Subprocess.new(["gnome-network-displays"], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            self.feedback.set_text("Could not open the GNOME Miracast picker.")
            self.present()

    def background_scan(self):
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self):
        if self.scanning or self.phase != "idle":
            return
        self.scanning = True
        self.refresh_button.set_sensitive(False)
        if not self.devices:
            self.render_devices()

        def scan():
            try:
                devices = asyncio.run(discover())
                GLib.idle_add(self.finish_scan, devices, False)
            except Exception:
                LOG.exception("Wireless display discovery failed")
                GLib.idle_add(self.finish_scan, [], True)

        threading.Thread(target=scan, daemon=True).start()

    def finish_scan(self, devices, failed):
        self.scanning = False
        self.scan_error = failed
        has_changes = devices != self.devices or not devices
        self.devices = devices
        if has_changes:
            self.render_devices()
        else:
            self.refresh_button.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def render_devices(self):
        for container in (self.device_list, self.target_menu):
            for child in container.get_children():
                container.remove(child)
        targets = self.devices
        if not targets:
            if self.scanning:
                status = "Looking for displays…"
            elif self.scan_error:
                status = "Discovery failed. Refresh to try again."
            else:
                status = "No displays found. Check that your TV is on and on the same network."
            self.device_list.pack_start(styled(label(status, "support"), "empty-devices"), False, False, 0)
            item = self.menu_item(self.target_menu, "No displays available" if not self.scanning else status, lambda: None)
            item.set_sensitive(False)

        for device in targets:
            button = self.action("", lambda item=device: self.start_cast(item), "device-button")
            row = Gtk.Box(spacing=10)
            row.pack_start(label(device.name, "form-title"), True, True, 0)
            row.pack_end(label(device.protocol, "support"), False, False, 0)
            button.add(row)
            button.get_accessible().set_name(f"Cast to {device.name} over {device.protocol}")
            self.device_list.pack_start(button, False, False, 0)
            self.menu_item(
                self.target_menu, f"{device.name} · {device.protocol}",
                lambda item=device: self.start_cast(item),
            )
        self.refresh_button.set_sensitive(not self.scanning)
        self.device_list.show_all()
        self.target_menu.show_all()

    def set_phase(self, phase, detail=None):
        self.phase = phase
        name = self.target.name if self.target else ""
        protocol = self.target.protocol if self.target else ""
        status = {
            "idle": ("STANDBY", "Choose a display", "Cast your screen and sound to a nearby TV or receiver."),
            "picking": ("WAITING", f"Choose what to cast to {name}", "Choose a screen or window in the system picker."),
            "connecting": ("CONNECTING", f"Connecting to {name}", f"Follow the system or TV prompts to start {protocol}."),
            "pair": ("PAIRING", f"Pair with {name}", "Enter the AirPlay password or the code shown on your TV."),
            "live": ("LIVE", f"Casting to {name}", f"{protocol} · {detail or 'Screen sharing active'}"),
        }
        flag, heading, message = status[phase]
        self.flag.set_text(flag)
        self.heading.set_text(heading)
        self.message.set_text(message)
        self.feedback.set_text("")
        style = self.flag.get_style_context()
        if phase == "live":
            style.add_class("live")
        else:
            style.remove_class("live")

        self.stack.set_visible_child_name("idle" if phase == "idle" else "casting")
        self.pairing.set_visible(phase == "pair")
        self.progress.set_visible(phase in {"picking", "connecting"})
        if phase in {"picking", "connecting"}:
            self.progress.start()
        else:
            self.progress.stop()

        self.stop_button.set_label("Stop casting" if phase == "live" else "Cancel cast")
        self.tray_stop.set_label(self.stop_button.get_label())
        self.tray_stop.set_visible(phase != "idle")
        self.tray_targets.set_sensitive(phase == "idle")
        self.indicator.set_icon_full(
            "screen-relay-live" if phase == "live" else "screen-relay-idle",
            "Screen Relay is casting" if phase == "live" else "Screen Relay",
        )
        if phase == "idle":
            self.render_devices()

    def start_cast(self, device):
        if self.phase != "idle":
            return
        self.present()
        self.target = device
        self.generation += 1
        generation = self.generation

        def report(kind, detail):
            GLib.idle_add(self.handle_session_event, generation, kind, detail)

        if device.protocol == "AirPlay":
            if not executable():
                self.failure("AirPlay is unavailable. Run the installer to add its mirroring component.")
                return
            self.set_phase("connecting")
            self.session = AirPlaySession(device, self.audio.get_active(), report)
            self.session.start()
            return

        missing = missing_plugins()
        if missing:
            LOG.warning("Missing GStreamer elements: %s", ", ".join(missing))
            self.failure("Media components are missing. See the installation instructions.")
            return
        try:
            monitor = playback_monitor() if self.audio.get_active() else None
            self.stream = CastStream(device, monitor, lambda: self.cast_ready(report), self.failure)
            self.portal = ScreenPortal(self.source_ready, self.failure, self.capture_closed)
            self.set_phase("picking")
            self.portal.start()
        except (GLib.Error, RuntimeError) as exc:
            self.failure(str(exc))

    def source_ready(self, node, fd):
        if self.phase != "picking":
            os.close(fd)
            return
        self.set_phase("connecting")
        try:
            self.stream.start(node, fd)
        except Exception:
            LOG.exception("Could not start Cast capture")
            self.failure("Could not start capture. Check the screen and network, then try again.")

    def cast_ready(self, report):
        self.session = CastSession(self.target, self.stream.url, bool(self.stream.monitor), report)
        self.session.start()

    def handle_session_event(self, generation, kind, detail):
        if generation != self.generation or self.phase == "idle":
            return GLib.SOURCE_REMOVE
        if kind == "failure":
            self.failure(detail)
        elif kind == "pair":
            self.set_phase("pair")
            title = "AirPlay password" if detail == "password" else "Code shown on your TV"
            self.pair_label.set_text(title)
            self.code.set_text("")
            self.pair_button.set_sensitive(False)
            self.code.get_accessible().set_name(title)
            self.present()
            self.code.grab_focus()
        else:
            self.set_phase(kind, detail)
        return GLib.SOURCE_REMOVE

    def submit_code(self):
        if self.phase == "pair" and (code := self.code.get_text().strip()):
            self.session.submit(code)
            self.code.set_text("")
            self.set_phase("connecting")

    def capture_closed(self):
        self.failure("The desktop ended the cast.")

    def release_cast(self, shutdown=False):
        self.generation += 1
        session, self.session = self.session, None
        stream, self.stream = self.stream, None
        portal, self.portal = self.portal, None
        self.target = None
        if session:
            if shutdown:
                session.shutdown()
            else:
                session.close()
        if stream:
            stream.close()
        if portal:
            portal.close()

    def stop(self):
        self.failure("Casting stopped." if self.phase == "live" else None)

    def failure(self, message):
        self.release_cast()
        self.code.set_text("")
        self.set_phase("idle")
        if message:
            self.feedback.set_text(message)
            if not self.window.is_visible() and message != "Casting stopped.":
                notice = Gio.Notification.new("Screen Relay")
                notice.set_body(message)
                self.send_notification("cast-status", notice)
