"""Unicode font styling for headings and brand text.

Telegram's HTML has no colour tags, so the bot's "colour" comes from emoji and
these Mathematical Alphanumeric typefaces (the same family as the 𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝
brand signature). Only letters and digits are mapped - spaces, punctuation and
non-Latin text pass through untouched.
"""

from __future__ import annotations

# Latin cap start, latin small start, digit start code points (Unicode
# Mathematical Alphanumeric Symbols - see fonts_table notes in the docstring).
_BLOCKS: dict[str, tuple[int, int, int] | None] = {
    # Mathematical Bold (the default heading weight)
    "bold": (0x1D400, 0x1D41A, 0x1D7CE),
    # Mathematical Sans-Serif Bold
    "bold_sans": (0x1D5D4, 0x1D5EE, 0x1D7EC),
    # Mathematical Italic
    "italic": (0x1D434, 0x1D44E, None),
    # Mathematical Bold Italic
    "bold_italic": (0x1D468, 0x1D482, None),
    # Mathematical Monospace
    "monospace": (0x1D670, 0x1D68A, 0x1D7F6),
    # Mathematical Double-Struck
    "double_struck": (0x1D538, 0x1D552, 0x1D7D8),
}

# Characters the offset blocks get wrong (Unicode historical gaps).
_EXCEPTIONS: dict[str, dict[str, str]] = {
    "italic": {"h": "\u210e"},
    "double_struck": {
        "C": "\u2102", "H": "\u210d", "N": "\u2115",
        "P": "\u2119", "Q": "\u211a", "R": "\u211d", "Z": "\u2124",
    },
}

# Black-letter (Fraktur) has holes in both ranges, so it is spelled out.
_FRAKTUR_UPPER = {
    "A": "\U0001d504", "B": "\U0001d505", "C": "\u212d", "D": "\U0001d507",
    "E": "\U0001d508", "F": "\U0001d509", "G": "\U0001d50a", "H": "\u210c",
    "I": "\u2111", "J": "\U0001d50d", "K": "\U0001d50e", "L": "\U0001d50f",
    "M": "\U0001d510", "N": "\U0001d511", "O": "\U0001d512", "P": "\U0001d513",
    "Q": "\U0001d514", "R": "\u211c", "S": "\U0001d516", "T": "\U0001d517",
    "U": "\U0001d518", "V": "\U0001d519", "W": "\U0001d51a", "X": "\U0001d51b",
    "Y": "\U0001d51c", "Z": "\u2128",
}
_FRAKTUR_LOWER_START = 0x1D51E

# Modifier-letter small caps (the "small caps" look).
_SMALL_CAPS = {
    "A": "\u1d00", "B": "\u0299", "C": "\u1d04", "D": "\u1d05", "E": "\u1d07",
    "F": "\ua730", "G": "\u0262", "H": "\u029c", "I": "\u026a", "J": "\u1d0a",
    "K": "\u1d0b", "L": "\u029f", "M": "\u1d0d", "N": "\u0274", "O": "\u1d0f",
    "P": "\u1d18", "Q": "Q", "R": "\u0280", "S": "\ua731", "T": "\u1d1b",
    "U": "\u1d1c", "V": "\u1d20", "W": "\u1d21", "X": "X", "Y": "\u028f",
    "Z": "\u1d22",
}


def _block_table(upper_start: int, lower_start: int, digit_start: int | None) -> dict[str, str]:
    table: dict[str, str] = {}
    for index in range(26):
        table[chr(ord("A") + index)] = chr(upper_start + index)
        table[chr(ord("a") + index)] = chr(lower_start + index)
    if digit_start is not None:
        for index in range(10):
            table[chr(ord("0") + index)] = chr(digit_start + index)
    return table


def _small_caps_table() -> dict[str, str]:
    table = {}
    for letter, glyph in _SMALL_CAPS.items():
        table[letter] = glyph
        table[letter.lower()] = glyph
    return table


FONTS: dict[str, dict[str, str]] = {
    **{name: _block_table(*block) for name, block in _BLOCKS.items() if block},
    "fraktur": {
        **_FRAKTUR_UPPER,
        **{chr(ord("a") + i): chr(_FRAKTUR_LOWER_START + i) for i in range(26)},
    },
    "small_caps": _small_caps_table(),
}
for _name, _exceptions in _EXCEPTIONS.items():
    FONTS[_name].update(_exceptions)

DEFAULT_FONT = "bold"


def stylize(text: str, font: str = DEFAULT_FONT) -> str:
    """Rewrite A-Z/a-z/0-9 in ``text`` with the chosen typeface.

    Unknown fonts raise ``ValueError`` - a typo should not silently change
    how every heading looks.
    """
    if font not in FONTS:
        raise ValueError(f"Unknown font {font!r}. Available: {', '.join(sorted(FONTS))}")
    table = FONTS[font]
    return "".join(table.get(char, char) for char in text or "")


def list_fonts() -> list[str]:
    return sorted(FONTS)


def is_stylized(text: str, font: str = DEFAULT_FONT) -> bool:
    """True when at least one character was actually rewritten."""
    if font not in FONTS:
        raise ValueError(f"Unknown font {font!r}")
    table = FONTS[font]
    return any(char in table and table[char] != char for char in text or "")


__all__ = ["FONTS", "DEFAULT_FONT", "stylize", "list_fonts", "is_stylized"]
