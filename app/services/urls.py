"""SSRF-safe target validation for every outbound media request.

Blocks: non-http schemes, credentials in URLs, private / loopback /
link-local / CGNAT / metadata addresses, disallowed ports, and hostname
shortcuts such as `localhost`. Re-run for *every* redirect hop.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

DEFAULT_PORTS = frozenset({80, 443})
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "metadata.goog",
    }
)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (cloud metadata lives here too)
    ipaddress.ip_network("192.0.0.0/24"),
]


class URLValidationError(Exception):
    """Carries a safe-to-display message plus a machine code."""

    def __init__(self, message: str, code: str = "blocked_destination") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _host_is_blocked(hostname: str) -> bool:
    host = hostname.strip().strip(".").lower()
    if not host:
        return True
    if host in BLOCKED_HOSTNAMES:
        return True
    if host.endswith((".localhost", ".local", ".internal", ".localdomain")):
        return True
    return False


def check_ip(address: str, allow_private: bool = False) -> None:
    """Raise when an IP address is not a permitted public destination."""
    if allow_private:
        return
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise URLValidationError(f"Unresolvable address: {address}", "blocked_destination") from exc

    if any(ip in net for net in _BLOCKED_NETWORKS):
        raise URLValidationError("That address is not a permitted download source.", "blocked_destination")
    blocked = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
    if blocked:
        raise URLValidationError("That address is not a permitted download source.", "blocked_destination")


async def resolve_addresses(hostname: str) -> list[str]:
    """Resolve A/AAAA records through the event loop (no blocking getaddrinfo)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLValidationError(f"Could not resolve host {hostname}", "dns_failure") from exc
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = sockaddr[0]
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise URLValidationError(f"Could not resolve host {hostname}", "dns_failure")
    return addresses


def parse_url(url: str) -> tuple[str, str, int]:
    """Return (scheme, hostname, port); raises URLValidationError when unsafe."""
    if not url or len(url) > 2048:
        raise URLValidationError("The link is empty or far too long.", "invalid_url")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise URLValidationError("That is not a valid link.", "invalid_url") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise URLValidationError("Only http and https links are supported.", "unsupported_scheme")
    if parts.username or parts.password:
        raise URLValidationError("Links containing credentials are not accepted.", "invalid_url")
    hostname = parts.hostname
    if not hostname:
        raise URLValidationError("The link has no hostname.", "invalid_url")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise URLValidationError("The link has an invalid port.", "invalid_url") from exc
    return scheme, hostname.lower(), port


async def validate_target(
    url: str,
    *,
    allow_private: bool = False,
    allowed_ports: frozenset[int] = DEFAULT_PORTS,
) -> str:
    """Validate scheme, host, DNS results and port for one request hop.

    Returns the normalised URL. Raises :class:`URLValidationError`.
    """
    scheme, hostname, port = parse_url(url)

    if port not in allowed_ports:
        raise URLValidationError(
            f"Port {port} is not in the allowed ports ({sorted(allowed_ports)}).",
            "blocked_destination",
        )
    if not allow_private and _host_is_blocked(hostname):
        raise URLValidationError(
            "That hostname points inside a local network.", "blocked_destination"
        )

    addresses = await resolve_addresses(hostname)
    for address in addresses:
        check_ip(address, allow_private=allow_private)

    parts = urlsplit(url.strip())
    path = parts.path or "/"
    return urlunsplit((scheme, hostname + (f":{port}" if port not in (80, 443) else ""), path, parts.query, ""))


def redact_url(url: str) -> str:
    """Strip query/fragment so signed or private URLs never reach the logs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    netloc = parts.hostname or "<unknown>"
    suffix = "?…" if parts.query else ""
    return f"{parts.scheme}://{netloc}{parts.path}{suffix}"


__all__ = [
    "URLValidationError",
    "DEFAULT_PORTS",
    "check_ip",
    "resolve_addresses",
    "parse_url",
    "validate_target",
    "redact_url",
]
