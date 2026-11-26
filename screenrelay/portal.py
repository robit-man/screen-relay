"""Permission-based screen capture through the XDG desktop portal."""

import os
import uuid

from gi.repository import Gio, GLib

SERVICE = "org.freedesktop.portal.Desktop"
ROOT = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"


def variant(signature, value):
    return GLib.Variant(signature, value)


class ScreenPortal:
    def __init__(self, ready, failed, closed):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.ready = ready
        self.failed = failed
        self.closed = closed
        self.request = None
        self.session = None
        self.request_signal = 0
        self.session_signal = 0
        self.active = True

    def start(self):
        token = "relay_" + uuid.uuid4().hex
        self.session = f"{ROOT}/session/{self.sender}/{token}"
        self.session_signal = self.bus.signal_subscribe(
            SERVICE, SESSION, "Closed", self.session, None,
            Gio.DBusSignalFlags.NONE, self._session_closed,
        )
        self._call("CreateSession", "(a{sv})", (), {
            "session_handle_token": variant("s", token),
        }, self._created)

    def _call(self, method, signature, args, options, next_step):
        token = "relay_" + uuid.uuid4().hex
        options["handle_token"] = variant("s", token)
        self.request = f"{ROOT}/request/{self.sender}/{token}"
        self._listen(self.request, next_step)
        expected = self.request

        def returned(bus, result):
            try:
                (actual,) = bus.call_finish(result).unpack()
            except GLib.Error:
                if self.active and self.request == expected:
                    self.failed("The desktop screen picker is unavailable.")
                return
            if self.active and self.request == expected and actual != expected:
                self.bus.signal_unsubscribe(self.request_signal)
                self.request = actual
                self._listen(actual, next_step)
            elif not self.active and actual != expected:
                self._close_remote(actual, REQUEST)

        self.bus.call(
            SERVICE, ROOT, SCREENCAST, method, variant(signature, (*args, options)),
            variant_type("(o)"), Gio.DBusCallFlags.NONE, 10000, None, returned,
        )

    def _listen(self, path, next_step):
        def response(_bus, _sender, _path, _interface, _signal, payload):
            if not self.active:
                return
            self.bus.signal_unsubscribe(self.request_signal)
            self.request_signal = 0
            self.request = None
            code, results = payload.unpack()
            if code == 0:
                next_step(results)
            elif code == 1:
                self.failed(None)
            else:
                self.failed("The desktop could not share that screen or window.")

        self.request_signal = self.bus.signal_subscribe(
            SERVICE, REQUEST, "Response", path, None, Gio.DBusSignalFlags.NONE, response,
        )

    def _created(self, results):
        actual = results["session_handle"]
        if actual != self.session:
            self.bus.signal_unsubscribe(self.session_signal)
            self.session = actual
            self.session_signal = self.bus.signal_subscribe(
                SERVICE, SESSION, "Closed", actual, None, Gio.DBusSignalFlags.NONE,
                self._session_closed,
            )
        self._call("SelectSources", "(oa{sv})", (self.session,), {
            "types": variant("u", 3),
            "multiple": variant("b", False),
            "cursor_mode": variant("u", 2),
        }, self._selected)

    def _selected(self, _results):
        self._call("Start", "(osa{sv})", (self.session, ""), {}, self._started)

    def _started(self, results):
        streams = results.get("streams")
        if not streams:
            self.failed("The desktop returned no screen to share.")
            return
        node, _properties = streams[0]

        def opened(bus, result):
            try:
                reply, fds = bus.call_with_unix_fd_list_finish(result)
                fd = fds.get(reply.unpack()[0])
            except (GLib.Error, AttributeError, IndexError):
                if self.active:
                    self.failed("The desktop could not open the PipeWire stream.")
                return
            if self.active:
                self.ready(node, fd)
            else:
                os.close(fd)

        self.bus.call_with_unix_fd_list(
            SERVICE, ROOT, SCREENCAST, "OpenPipeWireRemote",
            variant("(oa{sv})", (self.session, {})), variant_type("(h)"),
            Gio.DBusCallFlags.NONE, 10000, None, None, opened,
        )

    def _session_closed(self, *_args):
        if self.active:
            self.closed()

    def _close_remote(self, path, interface):
        self.bus.call(
            SERVICE, path, interface, "Close", None, None, Gio.DBusCallFlags.NONE,
            3000, None, None,
        )

    def close(self):
        self.active = False
        if self.request_signal:
            self.bus.signal_unsubscribe(self.request_signal)
        if self.session_signal:
            self.bus.signal_unsubscribe(self.session_signal)
        if self.request:
            self._close_remote(self.request, REQUEST)
        if self.session:
            self._close_remote(self.session, SESSION)


def variant_type(signature):
    return GLib.VariantType.new(signature)
