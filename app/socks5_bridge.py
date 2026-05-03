from __future__ import annotations

import select
import socket
import struct
import threading

import socks


class Socks5AuthBridge:
    """Expose a local unauthenticated SOCKS5 port for an authenticated upstream."""

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.listen_port = 0
        self._server: socket.socket | None = None
        self._closed = threading.Event()

    def start(self) -> int:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(64)
        self._server = server
        self.listen_port = server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()
        return self.listen_port

    def close(self) -> None:
        self._closed.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def _serve(self) -> None:
        assert self._server is not None
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream = None
        try:
            client.settimeout(30)
            header = self._recv_exact(client, 2)
            if not header or header[0] != 5:
                return
            self._recv_exact(client, header[1])
            client.sendall(b"\x05\x00")

            request = self._recv_exact(client, 4)
            if not request or request[0] != 5 or request[1] != 1:
                client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return

            atyp = request[3]
            if atyp == 1:
                dest_host = socket.inet_ntoa(self._recv_exact(client, 4))
            elif atyp == 3:
                length = self._recv_exact(client, 1)[0]
                dest_host = self._recv_exact(client, length).decode("utf-8")
            elif atyp == 4:
                dest_host = socket.inet_ntop(socket.AF_INET6, self._recv_exact(client, 16))
            else:
                client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            dest_port = struct.unpack("!H", self._recv_exact(client, 2))[0]

            upstream = socks.socksocket()
            upstream.set_proxy(
                socks.SOCKS5,
                self.host,
                self.port,
                username=self.username,
                password=self.password,
                rdns=True,
            )
            upstream.settimeout(30)
            upstream.connect((dest_host, dest_port))
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            self._relay(client, upstream)
        except Exception:
            try:
                client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            except OSError:
                pass
        finally:
            for sock in (client, upstream):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = sock.recv(size - len(chunks))
            if not chunk:
                raise OSError("connection closed")
            chunks.extend(chunk)
        return bytes(chunks)

    @staticmethod
    def _relay(left: socket.socket, right: socket.socket) -> None:
        left.settimeout(None)
        right.settimeout(None)
        sockets = [left, right]
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 300)
            if errored or not readable:
                return
            for src in readable:
                dst = right if src is left else left
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
