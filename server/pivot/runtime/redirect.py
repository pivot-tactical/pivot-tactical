"""Single-port HTTP -> HTTPS redirect for the TLS listener.

PIVOT serves everything over self-signed TLS on one configurable port (see
``tls.py`` — required for ``getUserMedia`` on a LAN address). Browsers default
bare ``host:port`` addresses to ``http://``, so a trainee revisiting
``http://192.168.1.20:8080`` hits a TLS-only socket with a plain-text request.
That fails the TLS handshake outright (``ERR_SSL_PROTOCOL_ERROR``) — the
request never reaches the app, so ``HTTPSRedirectMiddleware`` never gets a
chance to redirect it.

To actually redirect, the *same* public port has to understand both
protocols. This module binds that port with a tiny asyncio TCP sniffer: it
peeks at the first byte of each connection. A TLS ClientHello starts with
``0x16`` (handshake content type) — those connections are proxied verbatim to
the real uvicorn+TLS server, which listens on a loopback-only port. Anything
else is treated as plain HTTP: we read the request line and ``Host`` header
and reply with a redirect to the same host/path on ``https://``.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("pivot.redirect")

_TLS_HANDSHAKE_CONTENT_TYPE = 0x16
_HEADER_READ_TIMEOUT = 5.0
_MAX_HEADER_BYTES = 8192


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes from ``reader`` to ``writer`` until EOF or error."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _proxy_to_https(
    first_byte: bytes,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    https_port: int,
) -> None:
    """A real TLS connection: forward the bytes as-is to the loopback TLS server."""
    try:
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", https_port)
    except OSError:
        client_writer.close()
        return
    up_writer.write(first_byte)
    await up_writer.drain()
    await asyncio.gather(
        _pump(client_reader, up_writer),
        _pump(up_reader, client_writer),
    )


def _redirect_target(request: bytes, public_port: int) -> bytes:
    """Best-effort ``https://host[:port]/path`` for a plain-HTTP request."""
    lines = request.split(b"\r\n")
    path = b"/"
    if lines:
        parts = lines[0].split(b" ")
        if len(parts) >= 2:
            path = parts[1]
    host = b""
    for line in lines[1:]:
        if line[:5].lower() == b"host:":
            host = line.split(b":", 1)[1].strip()
            break
    if not host:
        host = b"localhost"
    elif host.startswith(b"["):
        # IPv6 literal, e.g. "[::1]:8080" -> "[::1]"
        end = host.find(b"]")
        host = host[: end + 1] if end != -1 else host
    else:
        host = host.split(b":")[0]
    suffix = b"" if public_port == 443 else b":" + str(public_port).encode("ascii")
    return b"https://" + host + suffix + path


async def _redirect_to_https(
    first_byte: bytes,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    public_port: int,
) -> None:
    """A plain-HTTP request: respond with a redirect to the https:// origin."""
    try:
        header = await asyncio.wait_for(
            client_reader.readuntil(b"\r\n\r\n"), timeout=_HEADER_READ_TIMEOUT
        )
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
        client_writer.close()
        return
    location = _redirect_target(first_byte + header, public_port)
    body = b"Redirecting to a secure connection..."
    response = (
        b"HTTP/1.1 301 Moved Permanently\r\n"
        b"Location: " + location + b"\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Connection: close\r\n"
        b"\r\n" + body
    )
    try:
        client_writer.write(response)
        await client_writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        client_writer.close()


def _make_handler(public_port: int, https_port: int):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await reader.read(1)
        except (ConnectionError, OSError):
            writer.close()
            return
        if not first:
            writer.close()
            return
        if first[0] == _TLS_HANDSHAKE_CONTENT_TYPE:
            await _proxy_to_https(first, reader, writer, https_port)
        else:
            await _redirect_to_https(first, reader, writer, public_port)

    return handle


async def serve_redirector(host: str, public_port: int, https_port: int) -> asyncio.AbstractServer:
    """Bind ``(host, public_port)``, redirecting plain HTTP to the TLS server.

    TLS connections are proxied to ``https_port`` on loopback, where the real
    uvicorn server is listening.
    """
    return await asyncio.start_server(_make_handler(public_port, https_port), host, public_port)
