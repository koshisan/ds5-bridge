"""TCP protocol framing and message types for DS5 Bridge."""

import struct
import json

# Message types
MSG_INPUT_REPORT = 0x01
MSG_OUTPUT_REPORT = 0x02
MSG_CONTROLLER_INFO = 0x03
MSG_PING = 0x04
MSG_PONG = 0x04  # Same type, differentiated by direction

HEADER_SIZE = 4  # 4-byte big-endian length prefix


def frame_message(msg_type: int, payload: bytes) -> bytes:
    """Frame a message with 4-byte big-endian length prefix + type byte + payload."""
    data = bytes([msg_type]) + payload
    return struct.pack(">I", len(data)) + data


def frame_input_report(report: bytes) -> bytes:
    """Frame a raw HID input report for sending to host."""
    return frame_message(MSG_INPUT_REPORT, report)


def frame_controller_info(controller_type: str, connection_mode: str) -> bytes:
    """Frame controller info as JSON."""
    info = {
        "controller_type": controller_type,
        "connection_mode": connection_mode,
    }
    return frame_message(MSG_CONTROLLER_INFO, json.dumps(info).encode("utf-8"))


def frame_ping() -> bytes:
    """Frame a ping message."""
    return frame_message(MSG_PING, b"ping")


def frame_pong() -> bytes:
    """Frame a ping message."""
    return frame_message(MSG_PING, b"pong")


def read_frame(sock) -> tuple[int, bytes] | None:
    """Read a single framed message from socket.

    Returns (msg_type, payload) or None if connection closed.
    """
    header = _recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None

    length = struct.unpack(">I", header)[0]
    if length == 0:
        return None

    data = _recv_exact(sock, length)
    if data is None:
        return None

    msg_type = data[0]
    payload = data[1:]
    return msg_type, payload


def _recv_exact(sock, n: int) -> bytes | None:
    """Receive exactly n bytes from socket. Returns None on disconnect."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (OSError, ConnectionError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
