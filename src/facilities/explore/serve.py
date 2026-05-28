"""Dev static server for the Explore module — with browser caching disabled.

Plain `python -m http.server` lets the browser heuristically cache ES modules,
so edits to src/*.js may not show up without a hard reload. This server sends
`Cache-Control: no-store` on every response, so each load fetches fresh files.

Run:   python serve.py     →   http://127.0.0.1:5173/
"""
import http.server
import os
import socketserver

PORT = 5173
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True   # rebind immediately on restart
    daemon_threads = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), NoCacheHandler) as httpd:
        print(f"Explore dev server (no-cache) -> http://127.0.0.1:{PORT}/")
        print(f"Host test harness            -> http://127.0.0.1:{PORT}/harness.html")
        httpd.serve_forever()
