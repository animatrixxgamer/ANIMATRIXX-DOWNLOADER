"""Downloader integration tests against a local HTTP server (no internet)."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.services.downloader import (
    DownloadCancelled,
    DownloadError,
    accept_file,
    download,
    sanitize_filename,
    sniff_media,
)

MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4096
MKV_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 4096
HTML_BYTES = b"<!DOCTYPE html><html><body>buy followers</body></html>"

ROUTES: dict[str, tuple[dict, bytes]] = {
    "/video.mp4": ({"Content-Type": "video/mp4"}, MP4_BYTES),
    "/movie.mkv": ({"Content-Type": "video/x-matroska"}, MKV_BYTES),
    "/page": ({"Content-Type": "text/html"}, HTML_BYTES),
    "/blob": ({"Content-Type": "application/octet-stream"}, MP4_BYTES),
    "/empty": ({"Content-Type": "video/mp4"}, b""),
    "/redirect": ({"Location": "/video.mp4", "Status": "301"}, b""),
    "/huge.mp4": ({"Content-Type": "video/mp4"}, MP4_BYTES),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib naming
        entry = ROUTES.get(self.path)
        if entry is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        headers, body = entry
        status = int(headers.get("Status", 200))
        if status in {301, 302}:
            self.send_response(status)
            self.send_header("Location", headers["Location"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(status)
        for key, value in headers.items():
            if key in {"Status"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port
    httpd.shutdown()
    thread.join(timeout=5)


def base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


async def run(port: int, path: str, dest: Path, **kwargs):
    options = dict(
        max_bytes=5 * 2**20,
        allow_private=True,
        allowed_ports=frozenset({port}),
        connect_timeout=5.0,
        read_timeout=5.0,
    )
    options.update(kwargs)
    return await download(f"{base(port)}{path}", dest, **options)


@pytest.mark.asyncio
async def test_download_streams_file(server, tmp_path):
    result = await run(server, "/video.mp4", tmp_path)
    assert result.path.exists()
    assert result.path.read_bytes() == MP4_BYTES
    assert result.total_bytes == len(MP4_BYTES)
    assert result.bytes_written == len(MP4_BYTES)
    assert result.filename == "video.mp4"
    # the only file in the temp dir is the finished one
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_matroska_magic_accepted(server, tmp_path):
    result = await run(server, "/movie.mkv", tmp_path)
    assert result.path.suffix == ".mkv"


@pytest.mark.asyncio
async def test_html_is_rejected_and_no_file_kept(server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        await run(server, "/page", tmp_path)
    assert exc.value.code == "file_type_rejected"
    assert exc.value.permanent is True
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_extensionless_blob_still_accepted_by_magic(server, tmp_path):
    result = await run(server, "/blob", tmp_path)
    assert result.path.exists()
    assert result.path.read_bytes() == MP4_BYTES


@pytest.mark.asyncio
async def test_size_cap_enforced(server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        await run(server, "/video.mp4", tmp_path, max_bytes=1024)
    assert exc.value.code == "size_limit"
    assert exc.value.permanent is True


@pytest.mark.asyncio
async def test_size_cap_rejects_from_headers_before_body(server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        await run(server, "/huge.mp4", tmp_path, max_bytes=100)
    assert exc.value.code == "size_limit"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_404_is_permanent(server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        await run(server, "/missing.mp4", tmp_path)
    assert exc.value.status == 404
    assert exc.value.permanent is True


@pytest.mark.asyncio
async def test_empty_file_is_an_error(server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        await run(server, "/empty", tmp_path)
    assert exc.value.code == "network"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_redirect_is_followed(server, tmp_path):
    result = await run(server, "/redirect", tmp_path)
    assert result.path.read_bytes() == MP4_BYTES


@pytest.mark.asyncio
async def test_redirect_loop_is_capped(server, tmp_path):
    # /redirect -> /redirect forever
    ROUTES["/loop"] = ({"Location": "/loop", "Status": "301"}, b"")
    try:
        with pytest.raises(DownloadError) as exc:
            await run(server, "/loop", tmp_path, max_redirects=3)
        assert exc.value.code == "too_many_redirects"
    finally:
        ROUTES.pop("/loop", None)


@pytest.mark.asyncio
async def test_disallowed_port_is_blocked(server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        await run(server, "/video.mp4", tmp_path, allowed_ports=frozenset({443}))
    assert exc.value.code == "blocked_destination"


@pytest.mark.asyncio
async def test_cancellation_stops_the_download(server, tmp_path):
    # /video.mp4 is tiny, so cancel before the first chunk
    with pytest.raises(DownloadCancelled):
        await run(server, "/video.mp4", tmp_path, should_cancel=lambda: True)
    # a finished file must never appear; the .part may stay for a later resume
    assert not (tmp_path / "video.mp4").exists()


@pytest.mark.asyncio
async def test_stale_partial_file_restarts_cleanly(server, tmp_path):
    (tmp_path / "video.mp4.part").write_bytes(b"garbage")
    result = await run(server, "/video.mp4", tmp_path)
    assert result.resumed is False
    assert result.path.read_bytes() == MP4_BYTES


@pytest.mark.asyncio
async def test_progress_reports_and_total(server, tmp_path):
    seen = []

    async def progress(done, total, speed):
        seen.append((done, total))

    await run(server, "/video.mp4", tmp_path, progress=progress)
    assert seen, "progress callback was never invoked"
    assert seen[-1][0] == len(MP4_BYTES)
    assert seen[-1][1] == len(MP4_BYTES)


def test_sanitize_filename():
    cleaned = sanitize_filename("../../etc/passwd")
    assert "/" not in cleaned and "\\" not in cleaned
    assert cleaned not in {"", ".", ".."}
    assert sanitize_filename("a/b/c.mp4") == "a_b_c.mp4"
    assert sanitize_filename("") == "download.bin"
    assert sanitize_filename("x" * 400 + ".mp4").endswith(".mp4")
    assert len(sanitize_filename("x" * 400 + ".mp4")) <= 180


def test_sniff_media():
    assert sniff_media(MP4_BYTES[:32]) == "mp4"
    assert sniff_media(MKV_BYTES[:32]) == "matroska"
    assert sniff_media(HTML_BYTES[:32]) == "html"
    assert sniff_media(b"short") is None


def test_accept_file_blocks_archives_and_pages():
    with pytest.raises(DownloadError):
        accept_file(b"", "text/html", "x.mp4")
    with pytest.raises(DownloadError):
        accept_file(b"PK\x03\x04rest", None, "x.mp4")
    with pytest.raises(DownloadError):
        accept_file(b"MZ\x90\x00rest", None, "x.exe")
    # unknown magic but an allowed extension is accepted (server gave no signature)
    accept_file(b"\x00\x00\x00\x00????", "application/octet-stream", "clip.mp4")
