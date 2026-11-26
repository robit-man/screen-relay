"""Run a private Doubletake AirPlay mirroring session."""

import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

LOG = logging.getLogger(__name__)


def executable():
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    bundled = data / "screen-relay/bin/doubletake"
    return str(bundled) if bundled.is_file() else shutil.which("doubletake")


def command(socket_path, action, **fields):
    message = json.dumps({"cmd": action, **fields}).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
        control.settimeout(3)
        control.connect(str(socket_path))
        control.sendall(message)
        with control.makefile("rb") as reply:
            return json.loads(reply.readline(262144))


class AirPlaySession:
    def __init__(self, device, audio, report):
        self.device = device
        self.audio = audio
        self.report = report
        self.cancelled = threading.Event()
        self.credentials = queue.Queue(maxsize=1)
        self.process = None
        self.worker = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.worker.start()

    def submit(self, code):
        try:
            self.credentials.put_nowait(code)
        except queue.Full:
            pass

    def run(self):
        binary = executable()
        if not binary:
            self.report("failure", "Install the AirPlay component, then try again.")
            return
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "screen-relay"
        try:
            config.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix="sr-air-", dir=os.environ.get("XDG_RUNTIME_DIR")) as work:
                socket_path = Path(work) / "control.sock"
                args = [
                    binary, "-daemonize", "-socket", str(socket_path),
                    "-creds", str(config / "airplay.json"), "-audio-source", "monitor",
                ]
                if not self.audio:
                    args.append("-no-audio")
                if self.cancelled.is_set():
                    return
                # Authentication values travel over the private control socket.
                env = os.environ.copy()
                env.pop("DOUBLETAKE_CODE", None)
                self.process = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, env=env,
                )
                try:
                    self.connect(socket_path)
                finally:
                    self.stop_process()
        except (OSError, ValueError, RuntimeError) as exc:
            if not self.cancelled.is_set():
                LOG.info("AirPlay session ended: %s", exc)
                self.report("failure", str(exc) if isinstance(exc, RuntimeError) else "Could not connect over AirPlay. Check the receiver and try again.")

    def connect(self, socket_path):
        deadline = time.monotonic() + 8
        while not socket_path.exists():
            if self.cancelled.wait(0.12):
                return
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("The AirPlay component could not start.")

        target = self.device.address
        response = command(
            socket_path, "connect", target=target, port=self.device.port, device_id=self.device.identity,
        )
        if not response.get("ok"):
            raise RuntimeError("The AirPlay receiver could not be reached.")
        previous_state = ""
        deadline = time.monotonic() + 120
        while not self.cancelled.wait(0.35):
            status = command(socket_path, "status")
            stream = next((item for item in status.get("streams", []) if item.get("device_ip") == target), None)
            if not stream:
                error = status.get("error", "")
                if error:
                    LOG.info("AirPlay receiver details: %s", error)
                raise RuntimeError(
                    "AirPlay casting ended." if previous_state == "streaming"
                    else "AirPlay could not start. Check the TV’s AirPlay settings and try again."
                )

            state = stream["state"]
            if state != previous_state:
                if state == "pin_required":
                    self.report("pair", stream.get("credential_kind", "pin"))
                elif state == "streaming":
                    self.report("live", "Desktop audio on" if stream.get("has_audio") else "Video only")
                else:
                    self.report("connecting", None)
                previous_state = state

            if state != "streaming" and time.monotonic() > deadline:
                raise RuntimeError("The AirPlay receiver took too long to connect.")
            if state == "pin_required":
                try:
                    code = self.credentials.get_nowait()
                except queue.Empty:
                    continue
                response = command(socket_path, "connect", target=target, pin=code)
                if not response.get("ok"):
                    raise RuntimeError("Pairing expired. Start casting again to get a fresh code.")
                previous_state = "connecting"

        if self.process.poll() is None:
            try:
                command(socket_path, "disconnect", target=target)
            except OSError:
                pass

    def stop_process(self):
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def close(self):
        self.cancelled.set()

    def shutdown(self):
        self.close()
        if self.worker.is_alive():
            self.worker.join(timeout=2)
        self.stop_process()
