"""Per-wake Unix-socket server with OS peer binding."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import socket
import struct

from .broker import Broker
from .protocol import ProtocolError, encode_message, receive_message


def peer_uid(connection: socket.socket) -> int:
    """Return the authenticated peer UID on Linux or macOS/BSD."""
    if hasattr(connection, "getpeereid"):
        uid, _gid = connection.getpeereid()  # type: ignore[attr-defined]
        return int(uid)
    if hasattr(socket, "SO_PEERCRED"):
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return int(uid)
    raise ProtocolError("this platform cannot authenticate Unix-socket peer credentials")


class SessionServer:
    """A socket is bound to one server-side session; callers cannot claim another role."""

    def __init__(self, broker: Broker, *, session_id: str, socket_path: str | Path,
                 expected_uid: int):
        self.broker = broker
        self.session_id = session_id
        self.socket_path = Path(socket_path)
        self.expected_uid = expected_uid
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def serve(self, *, max_requests: int | None = None) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            raise ProtocolError(f"refusing to replace existing socket: {self.socket_path}")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        handled = 0
        try:
            listener.bind(os.fspath(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            listener.listen(8)
            while not self._stop and (max_requests is None or handled < max_requests):
                connection, _ = listener.accept()
                with connection:
                    try:
                        if peer_uid(connection) != self.expected_uid:
                            response: dict[str, Any] = {
                                "schema": "sjg.response/v1", "status": "denied",
                                "error": "Unix-socket peer identity does not match this wake",
                            }
                        else:
                            response = self.broker.handle(self.session_id, receive_message(connection))
                    except Exception as exc:
                        response = {"schema": "sjg.response/v1", "status": "retryable",
                                    "error": f"protocol failure: {exc}"}
                    connection.sendall(encode_message(response))
                    handled += 1
        finally:
            listener.close()
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
