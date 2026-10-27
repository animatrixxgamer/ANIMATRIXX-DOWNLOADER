"""SSRF protection - the rules that keep the bot off your internal network."""

import pytest

from app.services.urls import (
    URLValidationError,
    check_ip,
    parse_url,
    redact_url,
    validate_target,
)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file.mp4",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com/",
        "",
        "http://",
    ],
)
def test_bad_schemes_rejected(url):
    with pytest.raises(URLValidationError) as exc:
        parse_url(url)
    assert exc.value.code in {"unsupported_scheme", "invalid_url"}


def test_credentials_in_url_rejected():
    with pytest.raises(URLValidationError):
        parse_url("https://user:pass@example.com/video.mp4")


def test_invalid_port_rejected():
    with pytest.raises(URLValidationError):
        parse_url("http://example.com:99999/video.mp4")


def test_normal_urls_parse():
    scheme, host, port = parse_url("https://example.com/path/v.mkv?a=1")
    assert (scheme, host, port) == ("https", "example.com", 443)
    assert parse_url("http://example.com:8080/x")[2] == 8080


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.0.0.53",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata endpoint
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "224.0.0.1",
    ],
)
def test_private_and_metadata_ips_blocked(address):
    with pytest.raises(URLValidationError):
        check_ip(address)


def test_public_ip_allowed():
    check_ip("93.184.216.34")
    check_ip("2606:2800:220:1:248:1893:25c8:1946")


def test_private_ips_allowed_only_when_explicitly_enabled():
    check_ip("127.0.0.1", allow_private=True)
    check_ip("10.0.0.5", allow_private=True)


@pytest.mark.asyncio
async def test_localhost_hostname_blocked_before_dns():
    with pytest.raises(URLValidationError) as exc:
        await validate_target("http://localhost/file.mp4")
    assert exc.value.code == "blocked_destination"


@pytest.mark.asyncio
async def test_internal_suffixes_blocked():
    for host in ("router.local", "db.internal", "svc.localhost"):
        with pytest.raises(URLValidationError):
            await validate_target(f"http://{host}/x")


@pytest.mark.asyncio
async def test_disallowed_port_blocked():
    with pytest.raises(URLValidationError) as exc:
        await validate_target("http://127.0.0.1:8765/x", allow_private=True)
    assert exc.value.code == "blocked_destination"


@pytest.mark.asyncio
async def test_private_flag_allows_loopback_end_to_end():
    url = await validate_target(
        "http://127.0.0.1:8765/x",
        allow_private=True,
        allowed_ports=frozenset({8765, 80, 443}),
    )
    assert url.startswith("http://127.0.0.1")


def test_redact_url_hides_query():
    assert redact_url("https://cdn.example.com/a.mp4?token=secret") == (
        "https://cdn.example.com/a.mp4?…"
    )
    assert redact_url("https://cdn.example.com/a.mp4") == "https://cdn.example.com/a.mp4"
