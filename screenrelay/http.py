"""Serve Cast media only to the receiver the user selected."""

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MEDIA = re.compile(r"(stream\.m3u8|segment\d{5,}\.ts)")
MIME = {"m3u8": "application/vnd.apple.mpegurl", "ts": "video/mp2t"}


class CastMediaServer(ThreadingHTTPServer):
    request_queue_size = 12

    def __init__(self, stream):
        self.stream = stream
        self.slots = threading.BoundedSemaphore(self.request_queue_size)
        super().__init__((stream.device.lan.host, 0), MediaHandler)
        self.thread = threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True,
        )
        self.thread.start()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(4)
        return connection, address

    def verify_request(self, _request, address):
        return self.stream.active and address[0] == self.stream.device.address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, _request, _address):
        pass

    def close(self):
        self.shutdown()
        self.server_close()


class MediaHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, data=b"", mime="text/plain", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command == "GET" and data:
            self.wfile.write(data)

    def do_GET(self):
        self.serve_media()

    def do_HEAD(self):
        self.serve_media()

    def do_OPTIONS(self):
        self.serve_media()

    def serve_media(self):
        stream = self.server.stream
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self.respond(404)
            return
        if (
            not stream.active or not path.startswith(stream.path)
            or not (match := MEDIA.fullmatch(path[len(stream.path):]))
        ):
            self.respond(404)
            return
        if self.command == "OPTIONS":
            self.respond(204)
            return
        filename = match[1]
        try:
            data = (stream.directory / filename).read_bytes()
        except OSError:
            self.respond(404)
            return
        mime = MIME[filename.rsplit(".", 1)[1]]
        range_header = self.headers.get("Range")
        if not range_header:
            self.respond(200, data, mime, {"Accept-Ranges": "bytes"})
            return
        match = re.fullmatch(r"bytes=(\d{0,18})-(\d{0,18})", range_header)
        if match is None or not any(match.groups()) or not data:
            self.respond(416, headers={"Content-Range": f"bytes */{len(data)}"})
            return
        first, last = match.groups()
        start = int(first) if first else max(0, len(data) - int(last))
        end = min(len(data) - 1, int(last)) if first and last else len(data) - 1
        if start > end:
            self.respond(416, headers={"Content-Range": f"bytes */{len(data)}"})
            return
        self.respond(206, data[start:end + 1], mime, {
            "Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes",
        })
