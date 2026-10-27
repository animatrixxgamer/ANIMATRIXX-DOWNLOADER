"""Appearance: Unicode fonts, boxed headings, coloured (emoji) buttons, settings."""

from __future__ import annotations

import pytest

from app.database import repositories as repo
from app.database.session import build_engine, build_session_factory, init_db, session_scope
from app.keyboards import inline as kb
from app.utils.fonts import DEFAULT_FONT, FONTS, is_stylized, list_fonts, stylize
from app.utils.formatting import box, footer


# ------------------------------------------------------------------- fonts
def test_every_font_rewrites_basic_latin():
    for name in list_fonts():
        styled = stylize("Animatrixx 2026", font=name)
        assert len(styled) == len("Animatrixx 2026"), name
        assert is_stylized("A", font=name), name


def test_default_font_is_available():
    assert DEFAULT_FONT in FONTS
    assert is_stylized("ANIMATRIXX", DEFAULT_FONT)


def test_plain_text_passes_through():
    assert stylize(" Season 01 · 2024! ", font="bold") != ""
    text = "S01E04"
    assert len(stylize(text, font="monospace")) == len(text)


def test_non_latin_is_untouched():
    assert stylize("나 혼자만 레벨업", font="bold") == "나 혼자만 레벨업"
    assert stylize("— «» —", font="fraktur") == "— «» —"


def test_italic_h_exception():
    assert stylize("h", font="italic") == "\u210e"


def test_double_struck_exceptions():
    assert stylize("CHNPQRZ", font="double_struck") == (
        "\u2102\u210d\u2115\u2119\u211a\u211d\u2124"
    )


def test_fraktur_exceptions():
    assert stylize("CHIRZ", font="fraktur") == "\u212d\u210c\u2111\u211c\u2128"


def test_small_caps_lowercases_to_modifiers():
    assert stylize("Anime", font="small_caps") == stylize("ANIME", font="small_caps")


def test_unknown_font_raises():
    with pytest.raises(ValueError) as exc:
        stylize("x", font="comic_sans")
    assert "Unknown font" in str(exc.value)


def test_is_stylized_false_for_unmapped_input():
    assert is_stylized("!!!", font="bold") is False


# -------------------------------------------------------------------- box
def test_box_header_is_styled_by_default():
    text = box("ANIMATRIXX ANIME")
    assert text.startswith("╭") and text.endswith("╮")
    assert stylize("ANIMATRIXX ANIME", DEFAULT_FONT) in text
    assert "ANIMATRIXX ANIME" not in text  # fully restyled, not partially


def test_box_can_be_plain():
    plain = box("ANIMATRIXX ANIME", font=None)
    assert "ANIMATRIXX ANIME" in plain
    assert "\U0001d400" not in plain


def test_box_still_reads_as_a_box():
    text = box("QUEUE")
    assert text.count("╮") == 1
    assert len(text) > 10


def test_footer_is_a_string_constant():
    assert isinstance(footer, str)
    assert footer.startswith("╰")


# ----------------------------------------------------------------- buttons
def test_all_buttons_carry_a_colour_icon():
    markup = kb.search_results_keyboard("tok", [], page=1, has_next=True)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels
    assert any("\U0001f195" in label or "📄" in label for label in labels)  # page icon

    detail = kb.detail_keyboard("tok", 0)
    detail_labels = [b.text for row in detail.inline_keyboard for b in row]
    assert all(any(ord(ch) > 0x2600 for ch in label) for label in detail_labels)

    for markup in (
        kb.confirm_keyboard("del", "1"),
        kb.series_keyboard(1),
        kb.noop_keyboard(),
    ):
        labels = [b.text for row in markup.inline_keyboard for b in row]
        assert all(any(ord(ch) > 0x2600 for ch in label) for label in labels), labels


def test_result_labels_keep_the_title_readable():
    from app.services.anime_metadata import MediaResult

    item = MediaResult(provider="anilist", media_id="1", title="Solo Leveling")
    markup = kb.search_results_keyboard("tok", [item], page=1, has_next=False)
    first = markup.inline_keyboard[0][0].text
    assert first.startswith("🎬")
    assert "Solo Leveling" in first


def test_long_titles_are_truncated_not_dropped():
    long_title = "Reborn as a Space Mercenary in Another World " * 4
    from app.services.anime_metadata import MediaResult

    item = MediaResult(provider="anilist", media_id="2", title=long_title)
    markup = kb.search_results_keyboard("tok", [item], page=1, has_next=False)
    label = markup.inline_keyboard[0][0].text
    assert len(label) <= kb.MAX_LABEL + 3  # icon + space + ellipsis


# -------------------------------------------------------------- settings
@pytest.fixture
async def db():
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield build_session_factory(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_setting_roundtrip(db):
    async with session_scope(db) as session:
        assert await repo.get_setting(session, "welcome_sticker") is None
        await repo.set_setting(session, "welcome_sticker", "CAACAgIAAxkBAAI", note="by 1")
    async with session_scope(db) as session:
        assert await repo.get_setting(session, "welcome_sticker") == "CAACAgIAAxkBAAI"
        await repo.set_setting(session, "welcome_sticker", "CAAC-new-id")
    async with session_scope(db) as session:
        assert await repo.get_setting(session, "welcome_sticker") == "CAAC-new-id"


@pytest.mark.asyncio
async def test_setting_delete(db):
    async with session_scope(db) as session:
        await repo.set_setting(session, "welcome_sticker", "x")
    async with session_scope(db) as session:
        assert await repo.delete_setting(session, "welcome_sticker") is True
        assert await repo.get_setting(session, "welcome_sticker") is None
        assert await repo.delete_setting(session, "welcome_sticker") is False