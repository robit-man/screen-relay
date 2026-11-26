"""Capture screen and desktop audio for the selected Google Cast receiver."""

import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from gi.repository import GLib, Gst

from .http import CastMediaServer
from .network import interfaces

LOG = logging.getLogger(__name__)
QUEUE = "queue max-size-buffers=0 max-size-bytes=0 max-size-time=500000000 leaky=downstream"
REQUIRED = ("pipewiresrc", "pulsesrc", "x264enc", "h264parse", "aacparse", "avenc_aac", "hlssink2")


def missing_plugins():
    return [name for name in REQUIRED if Gst.ElementFactory.find(name) is None]


class CastStream:
    def __init__(self, device, monitor, on_ready, on_failure):
        self.device = device
        self.monitor = monitor
        self.on_ready = on_ready
        self.on_failure = on_failure
        self.path = f"/{secrets.token_urlsafe(18)}/"
        self.active = self.ready = False
        self.pipeline = self.bus = self.http = self.temporary = None
        self.bus_signal = self.timer = 0
        self.fd = None

    def start(self, node, fd):
        self.fd = fd
        try:
            self.temporary = tempfile.TemporaryDirectory(
                prefix="sr-cast-", dir=GLib.get_user_runtime_dir(),
            )
            self.directory = Path(self.temporary.name)
            self.pipeline = self.build_pipeline(node)
            self.bus = self.pipeline.get_bus()
            self.bus.add_signal_watch()
            self.bus_signal = self.bus.connect("message", self.on_message)
            self.active = True
            self.http = CastMediaServer(self)
            self.started = time.monotonic()
            self.next_network_check = self.started + 4
            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Could not start screen capture.")
            self.timer = GLib.timeout_add(500, self.tick)
        except Exception:
            self.close()
            raise

    @property
    def url(self):
        return f"http://{self.device.lan.host}:{self.http.server_port}{self.path}stream.m3u8"

    def build_pipeline(self, node):
        audio = ""
        if self.monitor:
            audio = f"""
                pulsesrc name=desktop_audio ! {QUEUE} ! audioconvert ! audioresample !
                audio/x-raw,rate=48000,channels=2 ! avenc_aac bitrate=128000 !
                aacparse ! queue ! hls.audio
            """
        pipeline = Gst.parse_launch(f"""
            pipewiresrc fd={self.fd} path={int(node)} keepalive-time=100 client-name="Screen Relay" !
            {QUEUE} ! videoconvert ! videoscale add-borders=true ! videorate !
            video/x-raw,format=I420,width=1280,height=720,pixel-aspect-ratio=1/1,framerate=30/1 !
            x264enc tune=zerolatency speed-preset=veryfast key-int-max=30 bitrate=2800 bframes=0 !
            video/x-h264,profile=constrained-baseline ! h264parse config-interval=-1 !
            video/x-h264,stream-format=byte-stream,alignment=au ! queue ! hls.video
            hlssink2 name=hls max-files=9 playlist-length=5 target-duration=1 send-keyframe-requests=false
            {audio}
        """)
        hls = pipeline.get_by_name("hls")
        hls.set_property("location", str(self.directory / "segment%05d.ts"))
        hls.set_property("playlist-location", str(self.directory / "stream.m3u8"))
        source = pipeline.get_by_name("desktop_audio")
        if source:
            source.set_property("device", self.monitor)
            source.set_property("client-name", "Screen Relay")
        return pipeline

    def tick(self):
        now = time.monotonic()
        if now >= self.next_network_check:
            self.next_network_check = now + 4
            if self.device.lan not in interfaces():
                self.timer = 0
                self.on_failure("The network changed. Find the TV again to restart casting.")
                return GLib.SOURCE_REMOVE
        if not self.ready:
            try:
                playlist = (self.directory / "stream.m3u8").read_text()
            except OSError:
                playlist = ""
            if playlist.count("#EXTINF:") >= 3:
                self.ready = True
                self.on_ready()
            elif now - self.started > 18:
                self.timer = 0
                self.on_failure("No frames arrived. Choose a different screen or window.")
                return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def on_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            LOG.warning("Capture error from %s: %s", message.src.get_name(), error.message)
            self.on_failure("Capture stopped. Check the screen and audio output, then try again.")
        elif message.type == Gst.MessageType.EOS:
            self.on_failure("The selected screen or window stopped sending frames.")
        elif message.type == Gst.MessageType.LATENCY:
            self.pipeline.recalculate_latency()

    def close(self):
        self.active = self.ready = False
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.http:
            self.http.close()
            self.http = None
        if self.bus:
            self.bus.disconnect(self.bus_signal)
            self.bus.remove_signal_watch()
            self.bus = None
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None
