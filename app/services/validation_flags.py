"""Small, dependency-free flag parser for bot commands.

``/download https://host/f.mp4 --series Solo Leveling --ep S01E04``
becomes ``ParsedFlags(positional=[url], opts={"series": "Solo Leveling", ...})``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedFlags:
    positional: list[str] = field(default_factory=list)
    opts: dict[str, str] = field(default_factory=dict)


def parse_flags(args: list[str] | None) -> ParsedFlags:
    """Split command arguments into positional values and ``--key value`` pairs."""
    result = ParsedFlags()
    tokens = list(args or [])
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--"):
            body = token[2:]
            if not body:
                raise ValueError("Empty flag name")
            if "=" in body:
                key, _, value = body.partition("=")
                if not value:
                    raise ValueError(f"--{key} needs a value")
            else:
                if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
                    raise ValueError(f"--{body} needs a value")
                key, value = body, tokens[i + 1]
                i += 1
            result.opts[key.lower()] = value
        else:
            result.positional.append(token)
        i += 1
    return result


__all__ = ["ParsedFlags", "parse_flags"]
