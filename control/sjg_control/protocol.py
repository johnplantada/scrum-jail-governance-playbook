"""Small, closed local protocol. A2A/MCP adapters terminate outside this boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import socket
import struct


MAX_MESSAGE = 1024 * 1024


class ProtocolError(RuntimeError):
    pass


def encode_message(value: dict[str, Any]) -> bytes:
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE:
        raise ProtocolError("request exceeds the one-megabyte protocol limit")
    return struct.pack("!I", len(body)) + body


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise ProtocolError("connection closed before the complete message arrived")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_message(connection: socket.socket) -> dict[str, Any]:
    size = struct.unpack("!I", _read_exact(connection, 4))[0]
    if size > MAX_MESSAGE:
        raise ProtocolError("message exceeds the one-megabyte protocol limit")
    try:
        value = json.loads(_read_exact(connection, size))
    except json.JSONDecodeError as exc:
        raise ProtocolError("message is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("message must be a JSON object")
    return value


def request(socket_path: str | Path, value: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    path = Path(socket_path)
    if not path.is_absolute():
        raise ProtocolError("SJG wake socket must be an absolute path")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(os.fspath(path))
        connection.sendall(encode_message(value))
        return receive_message(connection)
    finally:
        connection.close()
