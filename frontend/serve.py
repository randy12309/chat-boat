"""Dev static server for the frontend with caching disabled, so browsers never serve stale JS/HTML.

Run: python serve.py [port]   (default 4200; the backend only allows CORS from localhost:4200)
"""
import socket
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()


class DualStackServer(ThreadingHTTPServer):
    """Listen on IPv4 and IPv6 so 'localhost' works whichever address a browser tries first."""

    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4200
    handler = partial(NoCacheHandler, directory=str(Path(__file__).parent))
    print(f"Serving frontend on http://localhost:{port}")
    DualStackServer(("::", port), handler).serve_forever()
