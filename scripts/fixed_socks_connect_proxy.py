#!/usr/bin/env python3
"""Expose a restricted local HTTP CONNECT proxy through an upstream SOCKS5 proxy."""

from __future__ import annotations

import argparse
import select
import socket
import socketserver
from dataclasses import dataclass

import socks


def address(value: str) -> tuple[str, int]:
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("expected HOST:PORT")
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= parsed_port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return host, parsed_port


def host_mapping(value: str) -> tuple[str, str]:
    host, separator, ip = value.partition("=")
    if not separator or not host or not ip:
        raise argparse.ArgumentTypeError("expected HOST=IP")
    try:
        socket.inet_pton(socket.AF_INET, ip)
    except OSError as exc:
        raise argparse.ArgumentTypeError("mapping target must be an IPv4 address") from exc
    return host.lower(), ip


@dataclass(frozen=True)
class ProxyConfig:
    socks_host: str
    socks_port: int
    mappings: dict[str, str]


class ConnectHandler(socketserver.StreamRequestHandler):
    server: "ConnectServer"

    def respond(self, status: str) -> None:
        self.wfile.write(f"HTTP/1.1 {status}\r\nConnection: close\r\n\r\n".encode())

    def handle(self) -> None:
        request_line = self.rfile.readline(8192).decode("ascii", "replace").strip()
        parts = request_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            self.respond("405 Method Not Allowed")
            return

        for _ in range(100):
            line = self.rfile.readline(8192)
            if line in {b"\r\n", b"\n", b""}:
                break
        else:
            self.respond("431 Request Header Fields Too Large")
            return

        host, separator, port_text = parts[1].rpartition(":")
        if not separator or not host:
            self.respond("400 Bad Request")
            return
        try:
            port = int(port_text)
        except ValueError:
            self.respond("400 Bad Request")
            return
        target_ip = self.server.config.mappings.get(host.lower())
        if target_ip is None or port != 443:
            self.respond("403 Forbidden")
            return

        upstream = socks.socksocket()
        upstream.set_proxy(
            socks.SOCKS5,
            self.server.config.socks_host,
            self.server.config.socks_port,
        )
        upstream.settimeout(15)
        try:
            upstream.connect((target_ip, port))
        except OSError:
            upstream.close()
            self.respond("502 Bad Gateway")
            return

        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self.wfile.flush()
        client = self.connection
        client.settimeout(None)
        upstream.settimeout(None)
        try:
            while True:
                readable, _, _ = select.select([client, upstream], [], [], 60)
                if not readable:
                    continue
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = upstream if source is client else client
                    destination.sendall(data)
        finally:
            upstream.close()


class ConnectServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: ProxyConfig):
        self.config = config
        super().__init__(address, ConnectHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", type=address, default=("127.0.0.1", 18080))
    parser.add_argument("--socks", type=address, required=True)
    parser.add_argument("--map", action="append", type=host_mapping, required=True)
    args = parser.parse_args()
    mappings = dict(args.map)
    config = ProxyConfig(args.socks[0], args.socks[1], mappings)
    with ConnectServer(args.listen, config) as server:
        print(
            f"restricted CONNECT proxy listening on {args.listen[0]}:{args.listen[1]} "
            f"for {', '.join(sorted(mappings))}",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
