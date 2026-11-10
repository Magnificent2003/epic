#!/usr/bin/env python3
"""Run the local questionnaire API from any working directory."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import platform
import socket
import struct
import sys
from wsgiref.simple_server import WSGIRequestHandler, make_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app  # noqa: E402


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # Default access logs contain paths and submission identifiers.
        pass


def local_ipv4_addresses() -> list[str]:
    """Find local interface addresses without contacting an external service."""
    addresses = set()
    if sys.platform.startswith("linux"):
        import fcntl

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                for _, name in socket.if_nameindex():
                    request = struct.pack("256s", name.encode()[:15])
                    try:
                        flags = fcntl.ioctl(probe.fileno(), 0x8913, request)  # SIOCGIFFLAGS
                        if not struct.unpack_from("H", flags, 16)[0] & 1:  # IFF_UP
                            continue
                        address = fcntl.ioctl(probe.fileno(), 0x8915, request)  # SIOCGIFADDR
                        addresses.add(socket.inet_ntoa(address[20:24]))
                    except OSError:
                        continue
        except OSError:
            pass
    try:
        addresses.update(
            result[4][0]
            for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        pass
    return [
        str(address)
        for address in sorted(ipaddress.IPv4Address(value) for value in addresses)
        if not (address.is_loopback or address.is_unspecified or address.is_multicast)
    ]


def print_access_urls(host: str, port: int) -> None:
    if host != "0.0.0.0":
        print(f"EPIC: http://{host}:{port}", flush=True)
        return
    print(f"EPIC 本机访问：http://127.0.0.1:{port}", flush=True)
    addresses = local_ipv4_addresses()
    is_wsl = "microsoft" in platform.release().lower()
    for address in addresses:
        print(f"局域网访问：http://{address}:{port}", flush=True)
    if not addresses:
        print(f"未能自动识别局域网 IP，请使用 http://本机局域网IP:{port} 访问。", flush=True)
    if is_wsl:
        print(
            f"如局域网无法访问，可在管理员 PowerShell 运行 scripts/wsl_lan.ps1 -Port {port} 配置此端口的防火墙。",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="EPIC本地问卷、报告生成与下载服务。")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--storage-root", type=Path, default=PROJECT_ROOT / "work_dir")
    parser.add_argument("--definition", type=Path, default=PROJECT_ROOT / "questionnaire" / "definition.json")
    args = parser.parse_args()
    app = create_app(definition_path=args.definition, storage_root=args.storage_root)
    with make_server(args.host, args.port, app, handler_class=QuietRequestHandler) as server:
        print_access_urls(args.host, server.server_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nEPIC API stopped.")
        finally:
            app.close(wait=False)


if __name__ == "__main__":
    main()
