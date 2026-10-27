"""Title normalisation and input parsing."""

from app.utils.validation import (
    episode_code,
    is_safe_filename,
    normalize_title,
    parse_channel_ref,
    parse_episode_ref,
    strip_quality_tokens,
)


def test_normalize_strips_punctuation_and_case():
    assert normalize_title("  Solo Leveling! ") == "solo leveling"
    assert normalize_title("Reborn as a Space Mercenary") == "reborn as a space mercenary"


def test_normalize_equates_equivalent_titles():
    assert normalize_title("K-drama: The Glory") == normalize_title("k drama the glory")


def test_normalize_handles_empty():
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


def test_strip_quality_tokens():
    assert strip_quality_tokens("Show Name 1080p x265") == "Show Name x265"


def test_parse_episode_ref_formats():
    assert parse_episode_ref("S01E04") == (1, 4)
    assert parse_episode_ref("s2e13") == (2, 13)
    assert parse_episode_ref("1x4") == (1, 4)


def test_parse_episode_ref_rejects_garbage():
    assert parse_episode_ref("episode four") is None
    assert parse_episode_ref("") is None


def test_episode_code():
    assert episode_code(1, 4) == "S01E04"
    assert episode_code(12, 200) == "S12E200"


def test_parse_channel_ref():
    assert parse_channel_ref("-1001234567890") == -1001234567890
    assert parse_channel_ref("@MyChannel") == "@MyChannel"
    assert parse_channel_ref("@ab") is None
    assert parse_channel_ref("just a name") is None
    assert parse_channel_ref("") is None


def test_safe_filename():
    assert is_safe_filename("episode_01.mkv")
    assert not is_safe_filename("../etc/passwd")
    assert not is_safe_filename("a/b.mkv")
    assert not is_safe_filename("")
    assert not is_safe_filename("..")
