"""
A stand-in for Supabase in the kind smoke test: every PostgREST read is empty and every write succeeds, so the
services start and handlers run without a database. Standard library only.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Stub(BaseHTTPRequestHandler):
    def _reply(self, status: int) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = b"[]"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Range", "*/0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(200)

    def do_HEAD(self) -> None:
        self._reply(200)

    def do_POST(self) -> None:
        self._reply(201)

    def do_PATCH(self) -> None:
        self._reply(200)

    def do_DELETE(self) -> None:
        self._reply(200)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.command} {self.path}", flush=True)


ThreadingHTTPServer(("0.0.0.0", 8080), Stub).serve_forever()
