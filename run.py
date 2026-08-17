#!/usr/bin/env python3
"""
run.py — start the server and open a browser.

    uv run run.py

The target is: download, one command, browser opens. The user is not assumed to
be a developer, so anything that can be decided automatically is.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def find_open_port(preferred: int = 8000) -> int:
    """Prefer 8000, but take any free port rather than failing to start."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("No free port available.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Teep Analysis — local web app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn

    from backend import config

    if not (config.DIST / "index.html").exists():
        print(
            "The frontend bundle is missing from dist/.\n"
            "Build it once with:  npm --prefix frontend install && "
            "npm --prefix frontend run build",
            file=sys.stderr,
        )

    for path, what in (
        (config.REFERENCE_VIDEO, "reference video"),
        (config.REFERENCE_CSV, "reference metrics CSV"),
        (config.REFERENCE_PROVENANCE, "reference provenance"),
    ):
        if not path.exists():
            print(f"Missing {what}: {path}", file=sys.stderr)

    port = args.port if args.port else find_open_port()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((args.host, port))
    except OSError:
        port = find_open_port(port)

    url = f"http://{args.host}:{port}"

    if not args.no_browser:
        def open_when_ready() -> None:
            for _ in range(100):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    if probe.connect_ex((args.host, port)) == 0:
                        webbrowser.open(url)
                        return
                time.sleep(0.1)

        threading.Thread(target=open_when_ready, daemon=True).start()

    print(f"Teep Analysis running at {url}  (ctrl-c to stop)")
    uvicorn.run("backend.main:app", host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
