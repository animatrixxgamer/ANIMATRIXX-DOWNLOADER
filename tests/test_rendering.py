"""Every message builder must return text, not a callable or a repr.

This suite exists because ``footer`` was once a function that several
handlers interpolated into f-strings - the failure only showed up when a
message was actually rendered.
"""

from pathlib import Path
from types import SimpleNamespace

from app.handlers.search import render_detail, render_results
from app.services.anime_metadata import MediaResult, SearchResult
from app.services.progress import render_job
from app.utils.formatting import box, esc, footer, progress_bar

BROKEN_MARKERS = ("<function", "object at 0x")


def assert_text(value: str) -> str:
    assert isinstance(value, str), f"expected str, got {type(value).__name__}"
    for marker in BROKEN_MARKERS:
        assert marker not in value, f"message contains {marker!r}: {value[:200]}"
    return value


def make_job(**kw) -> SimpleNamespace:
    base = dict(
        id=12,
        state="downloading",
        source_type="url",
        source_url="https://cdn.example.com/ep01.mp4",
        filename="ep01.mp4",
        profile="balanced",
        resolution=None,
        series_id=3,
        season_number=1,
        episode_number=4,
        owner_telegram_id=1,
        attempts=1,
        percent=42.5,
        total_bytes=4_000_000,
        done_bytes=1_700_000,
        speed_bps=2_000_000,
        stage_detail="downloading",
        last_error=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_box_and_footer_are_plain_strings():
    assert_text(box("ANIMATRIXX"))
    assert_text(footer)
    assert_text(progress_bar(50))
    assert_text(esc("<b>&"))


def test_render_job_in_every_stage():
    for state in ("queued", "downloading", "encoding", "uploading", "failed", "completed"):
        text = assert_text(render_job(make_job(state=state)))
        assert "#12" in text
        assert state.upper() in text


def test_render_job_without_optional_fields():
    text = assert_text(
        render_job(
            make_job(
                state="queued",
                series_id=None,
                percent=None,
                total_bytes=None,
                done_bytes=None,
                speed_bps=None,
                stage_detail=None,
                last_error="something went wrong",
            )
        )
    )
    assert "something went wrong" in text


def test_render_job_unknown_total_is_honest():
    text = assert_text(
        render_job(make_job(total_bytes=None, done_bytes=1024, percent=None))
    )
    assert "total unknown" in text


def _result(page=1, has_next=True) -> SearchResult:
    return SearchResult(
        items=[
            MediaResult(
                provider="anilist",
                media_id="1",
                title="Solo Leveling",
                year=2024,
                media_type="anime",
                synopsis="An underdog hunter rises.",
                genres=["Action"],
                score=8.3,
            )
        ],
        page=page,
        has_next=has_next,
    )


def test_render_results_is_html_text():
    text, keyboard = render_results(_result(), "anime", "solo leveling", "abcd1234")
    assert_text(text)
    assert "Solo Leveling" in text
    assert keyboard is not None


def test_render_results_empty_still_renders():
    text, keyboard = render_results(SearchResult(items=[]), "drama", "nothing", "tok")
    assert_text(text)
    assert "No matching titles" in text
    assert keyboard is None


def test_render_detail_is_html_text():
    item = _result().items[0]
    text = assert_text(render_detail(item, archived_count=2))
    assert "metadata only" in text
    assert "2 episode(s)" in text
