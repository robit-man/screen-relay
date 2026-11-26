"""Drive the Google Cast default media receiver and track its playback."""

import logging
import threading
import time
import uuid

from pychromecast import get_chromecast_from_host
from pychromecast.error import PyChromecastError

LOG = logging.getLogger(__name__)


class CastSession:
    def __init__(self, device, url, audio, report):
        self.device = device
        self.url = url
        self.audio = audio
        self.report = report
        self.cancelled = threading.Event()
        self.worker = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.worker.start()

    def run(self):
        cast = None
        rejected = threading.Event()

        def loaded(success, _response):
            if not success:
                rejected.set()

        try:
            target = self.device
            cast = get_chromecast_from_host(
                (target.address, target.port, uuid.UUID(target.identity), target.model, target.name),
                tries=1, retry_wait=0.3, timeout=4,
            )
            cast.wait(timeout=6)
            if self.cancelled.is_set():
                return
            player = cast.media_controller
            player.play_media(
                self.url, "application/x-mpegurl", title="Screen Relay", stream_type="LIVE",
                media_info={"hlsSegmentFormat": "ts", "hlsVideoSegmentFormat": "mpeg2_ts"},
                callback_function=loaded,
            )

            started = False
            deadline = time.monotonic() + 28
            while not self.cancelled.wait(0.3):
                status = player.status
                owns_playback = status.content_id == self.url
                if owns_playback and status.player_state == "PLAYING":
                    if not started:
                        self.report("live", "Desktop audio on" if self.audio else "Video only")
                    started = True
                elif started and (not owns_playback or status.player_is_idle):
                    raise RuntimeError("The TV ended the cast.")
                elif rejected.is_set() or (owns_playback and status.idle_reason == "ERROR"):
                    raise RuntimeError("The TV could not play the cast. Check its network connection and try again.")
                elif not started and time.monotonic() > deadline:
                    raise RuntimeError("The TV did not start playback. Check that it can reach this computer.")
        except (OSError, PyChromecastError, RuntimeError) as exc:
            if not self.cancelled.is_set():
                LOG.info("Google Cast session ended: %s", type(exc).__name__)
                self.report("failure", str(exc) if isinstance(exc, RuntimeError) else "Could not connect to the TV over Google Cast.")
        finally:
            if cast:
                try:
                    player = cast.media_controller
                    if player.status.content_id == self.url and not player.status.player_is_idle:
                        player.stop()
                except (OSError, PyChromecastError):
                    pass
                try:
                    cast.disconnect(timeout=2)
                except (OSError, PyChromecastError):
                    pass

    def close(self):
        self.cancelled.set()

    def shutdown(self):
        self.close()
        if self.worker.is_alive():
            self.worker.join(timeout=3)
