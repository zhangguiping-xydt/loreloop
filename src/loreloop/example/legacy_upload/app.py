"""Tiny legacy upload service used by the five-minute LoreLoop walkthrough."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_UPLOAD_MIB = 5


def upload_allowed(size_mib: int) -> bool:
    """The public upload policy uses an inclusive size ceiling."""
    return 0 <= size_mib <= MAX_UPLOAD_MIB


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/":
            self.send_error(404)
            return
        body = (
            "<!doctype html><title>Legacy Upload</title>"
            f"<h1>Legacy Upload</h1><p>Upload limit: {MAX_UPLOAD_MIB} MiB</p>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
