"""A tiny recording HTTP proxy for tests: CONNECT tunnels and absolute-form requests.
Optionally requires Basic auth (like QuotaGuard). Local only."""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import urlsplit


class RecordingProxy:
    def __init__(self, user: str | None = None, password: str | None = None) -> None:
        self.seen: list[str] = []          # "CONNECT host:port" or "GET http://..."
        self.denied = 0
        self._auth = (base64.b64encode(f"{user}:{password}".encode()).decode()
                      if user else None)
        self.port = 0
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def _pipe(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while data := await r.read(65536):
                w.write(data)
                await w.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            w.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        headers = {k.lower(): v for k, v in (ln.split(": ", 1) for ln in lines[1:] if ": " in ln)}
        if self._auth and headers.get("proxy-authorization") != f"Basic {self._auth}":
            self.denied += 1
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                         b"Proxy-Authenticate: Basic\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if method == "CONNECT":
            host, port = target.rsplit(":", 1)
            self.seen.append(f"CONNECT {target}")
            ur, uw = await asyncio.open_connection(host, int(port))
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        else:
            u = urlsplit(target)
            self.seen.append(f"{method} {target}")
            ur, uw = await asyncio.open_connection(u.hostname, u.port or 80)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            rest = [ln for ln in lines[1:] if ln and not ln.lower().startswith("proxy-")]
            uw.write((f"{method} {path} {version}\r\n" + "\r\n".join(rest) + "\r\n\r\n")
                     .encode("latin-1"))
            await uw.drain()
        await asyncio.gather(self._pipe(reader, uw), self._pipe(ur, writer))
