#!/usr/bin/env python3
"""Run the local questionnaire API from any working directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from wsgiref.simple_server import WSGIRequestHandler, make_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app  # noqa: E402


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # Default access logs contain paths and submission identifiers.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="EPIC本地问卷；当前提供填写、核对与保存。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--storage-root", type=Path, default=PROJECT_ROOT / "work_dir")
    parser.add_argument("--definition", type=Path, default=PROJECT_ROOT / "questionnaire" / "definition.json")
    args = parser.parse_args()
    app = create_app(definition_path=args.definition, storage_root=args.storage_root)
    with make_server(args.host, args.port, app, handler_class=QuietRequestHandler) as server:
        print(f"EPIC: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nEPIC API stopped.")


if __name__ == "__main__":
    main()
