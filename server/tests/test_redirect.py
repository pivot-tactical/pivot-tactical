"""Single-port HTTP -> HTTPS redirect (pivot.runtime.redirect)."""

from __future__ import annotations

import asyncio
import socket

import pytest

from pivot.runtime.redirect import _redirect_target, serve_redirector


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_plain_http_request_gets_redirected_to_https():
    port = _free_port()
    redirector = await serve_redirector("127.0.0.1", port, https_port=1)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /some/path HTTP/1.1\r\nHost: 192.168.1.50:8080\r\n\r\n")
        await writer.drain()

        response = await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
    finally:
        redirector.close()
        await redirector.wait_closed()

    status_line, *header_lines = response.split(b"\r\n")
    assert status_line == b"HTTP/1.1 301 Moved Permanently"
    headers = dict(line.split(b": ", 1) for line in header_lines if b": " in line)
    assert headers[b"Location"] == f"https://192.168.1.50:{port}/some/path".encode()


@pytest.mark.asyncio
async def test_tls_connection_is_proxied_through_to_the_https_server():
    received = bytearray()
    done = asyncio.Event()

    async def echo(reader, writer):
        data = await reader.read(100)
        received.extend(data)
        writer.write(b"upstream-reply")
        await writer.drain()
        writer.close()
        done.set()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    https_port = upstream.sockets[0].getsockname()[1]

    redirector = await serve_redirector("127.0.0.1", 0, https_port=https_port)
    try:
        port = redirector.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        # 0x16 = TLS handshake content type -- looks like a ClientHello.
        tls_like = b"\x16\x03\x01\x00\x10fake-client-hello"
        writer.write(tls_like)
        await writer.drain()

        await asyncio.wait_for(done.wait(), timeout=5)
        reply = await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
    finally:
        redirector.close()
        await redirector.wait_closed()
        upstream.close()
        await upstream.wait_closed()

    assert bytes(received) == tls_like
    assert reply == b"upstream-reply"


def test_redirect_target_defaults_to_localhost_without_host_header():
    request = b"GET / HTTP/1.1\r\n\r\n"
    assert _redirect_target(request, 8080) == b"https://localhost:8080/"


def test_redirect_target_omits_port_443():
    request = b"GET /x HTTP/1.1\r\nHost: example.com:80\r\n\r\n"
    assert _redirect_target(request, 443) == b"https://example.com/x"


def test_redirect_target_handles_ipv6_host():
    request = b"GET /x HTTP/1.1\r\nHost: [::1]:8080\r\n\r\n"
    assert _redirect_target(request, 8080) == b"https://[::1]:8080/x"
