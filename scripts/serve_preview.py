"""Serve the isolated preview build with correct MIME types.

`python -m http.server` inherits the Windows registry mapping, and on some hosts
`.js` maps to text/plain. Browsers refuse to execute ES modules served that way, so the
preview shows an empty sign-in shell. This server pins the mappings:

    python scripts/serve_preview.py --port 8090
    # open http://127.0.0.1:8090/

It serves preview/dist only, never the real application, and binds to loopback.
"""
import argparse
import functools
import http.server
import mimetypes
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "preview" / "dist"


class PreviewHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[preview]", fmt % args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    mimetypes.add_type("text/javascript", ".js")
    if not DIST.is_dir():
        raise SystemExit(
            f"{DIST} is missing. Build it first: python preview/build.py"
        )

    handler = functools.partial(PreviewHandler, directory=str(DIST))
    with socketserver.TCPServer((args.host, args.port), handler) as server:
        print(f"Preview (sample data only): http://{args.host}:{args.port}/")
        print("Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
