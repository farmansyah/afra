"""A very small Chrome DevTools Protocol client (a WebSocket on 127.0.0.1 only), so A.F.R.A needs no extra package.

It talks to a browser A.F.R.A started itself on a loopback debugging port. Nothing here ever types: pages are driven with
Page.navigate and Runtime.evaluate (clicks through script), never with Input.* events, so no password or key can be typed.
"""
import base64
import hashlib
import json
import os
import socket
import struct
from urllib.parse import urlparse

import httpx


class CDPError(Exception):
    pass


class WS:
    """RFC 6455 client, text frames only, for ws://127.0.0.1:<port>/..."""

    def __init__(self, url: str, timeout: float = 30):
        u = urlparse(url)
        if u.scheme != "ws" or u.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise CDPError("Only a browser on this computer can be driven.")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        self.sock.sendall((f"GET {path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("The browser closed the connection.")
            head += chunk
        head, _, self.buf = head.partition(b"\r\n\r\n")
        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if b" 101 " not in head.split(b"\r\n")[0] or want not in head:
            raise CDPError("The browser refused the debugging connection.")

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("The browser closed the connection.")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str, opcode: int = 1):
        data = text.encode("utf-8") if isinstance(text, str) else text
        n = len(data)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        message, op0 = b"", 1
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)  # servers never mask
            if op == 8:
                raise CDPError("The browser closed the connection.")
            if op == 9:
                self.send(payload, 10)
                continue
            if op == 10:
                continue
            if op in (1, 2):
                op0 = op
            message += payload
            if b1 & 0x80:
                return message.decode("utf-8", "replace") if op0 == 1 else message.decode("latin-1")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Tab:
    """One DevTools target (a page, or the browser itself): call(method, params), eval(expression)."""

    def __init__(self, ws_url: str, timeout: float = 30):
        self.ws, self.n = WS(ws_url, timeout), 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise CDPError(f"{method}: {m['error'].get('message', 'failed')}")
                return m.get("result") or {}

    def eval(self, expression: str):
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise CDPError("The page script failed: " + str((r["exceptionDetails"].get("exception") or {}).get("description") or r["exceptionDetails"].get("text"))[:200])
        return (r.get("result") or {}).get("value")

    def close(self):
        self.ws.close()


def alive(endpoint: str) -> bool:
    try:
        return httpx.get(endpoint + "/json/version", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def pages(endpoint: str) -> list[dict]:
    return [t for t in httpx.get(endpoint + "/json/list", timeout=5).json() if t.get("type") == "page"]


def browser_tab(endpoint: str) -> Tab:
    return Tab(httpx.get(endpoint + "/json/version", timeout=5).json()["webSocketDebuggerUrl"])


def new_page(endpoint: str, url: str = "about:blank") -> dict:
    r = httpx.put(endpoint + "/json/new?" + url, timeout=10)
    if r.status_code >= 400:  # older builds only take GET
        r = httpx.get(endpoint + "/json/new?" + url, timeout=10)
    r.raise_for_status()
    return r.json()
