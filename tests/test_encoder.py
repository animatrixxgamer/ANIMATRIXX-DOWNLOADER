"""FFmpeg argument construction, scaling rules and progress maths."""

from pathlib import Path

import pytest

from app.services.encoder import (
    EncodeError,
    MediaInfo,
    build_encode_args,
    parse_progress_line,
    profile_label,
    scale_filter,
    target_bitrate_kbps,
)


def make_info(**kw) -> MediaInfo:
    base = dict(
        path=Path("/tmp/in.mkv"),
        duration=1400.0,
        width=1920,
        height=1080,
        fps=23.976,
        video_codec="h264",
        audio_codecs=["aac"],
        subtitle_codecs=["ass"],
        container="matroska,webm",
        size_bytes=500_000_000,
        has_audio=True,
    )
    base.update(kw)
    return MediaInfo(**base)


def test_original_profile_remuxes_without_transcoding():
    args = build_encode_args(
        Path("in.mkv"), Path("out.mkv"), profile="original",
        info=make_info(), out_format="mkv",
    )
    assert "copy" in args
    assert not any(a == "libx264" for a in args)


def test_balanced_profile_sets_crf():
    args = build_encode_args(
        Path("in.mkv"), Path("out.mp4"), profile="balanced",
        info=make_info(), out_format="mp4",
    )
    assert "libx264" in args
    assert args[args.index("-crf") + 1] == "23"


def test_high_profile_better_crf_small_profile_worse():
    high = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile="high", info=make_info(), out_format="mp4")
    small = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile="small", info=make_info(), out_format="mp4")
    assert high[high.index("-crf") + 1] == "18"
    assert small[small.index("-crf") + 1] == "28"


def test_incompatible_audio_is_transcoded_for_mp4():
    info = make_info(audio_codecs=["dts"], has_audio=True)
    args = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile="balanced", info=info, out_format="mp4")
    assert args[args.index("-c:a") + 1] == "aac"


def test_compatible_audio_stays_copied_in_mp4():
    args = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile="balanced", info=make_info(), out_format="mp4")
    assert args[args.index("-c:a") + 1] == "copy"


def test_mkv_keeps_everything_including_subtitles():
    args = build_encode_args(Path("a.mkv"), Path("b.mkv"), profile="balanced", info=make_info(), out_format="mkv")
    assert "-sn" not in args


def test_mp4_drops_unsupported_subtitles_deliberately():
    args = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile="balanced", info=make_info(), out_format="mp4")
    assert "-sn" in args


def test_audioless_source_gets_an():
    info = make_info(audio_codecs=[], has_audio=False)
    args = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile="balanced", info=info, out_format="mp4")
    assert "-an" in args


def test_no_upscale_filter_dropped_when_source_is_small():
    info = make_info(height=720, width=1280)
    assert scale_filter("720p", info) is None
    assert scale_filter("1080p", info) is None
    assert scale_filter("480p", info) is not None


def test_scale_filter_never_stretches():
    filter_str = scale_filter("720p", make_info())
    assert filter_str == "scale=-2:min(ih\\,720)"  # -2 keeps aspect ratio


def test_scale_filter_rejects_unknown_resolution():
    with pytest.raises(EncodeError):
        scale_filter("1440p", make_info())


def test_resolution_flag_lands_in_args():
    args = build_encode_args(
        Path("a.mkv"), Path("b.mp4"), profile="balanced",
        info=make_info(height=1080), out_format="mp4", resolution="720p",
    )
    assert "-vf" in args
    assert "scale" in args[args.index("-vf") + 1]


def test_target_profile_uses_two_pass_bitrate():
    args = build_encode_args(
        Path("a.mkv"), Path("b.mp4"), profile="target",
        info=make_info(), out_format="mp4", target_size_mb=100, two_pass=True, pass_number=1,
    )
    assert "-pass" in args
    assert args[args.index("-pass") + 1] == "1"
    assert "-b:v" in args
    bitrate = int(args[args.index("-b:v") + 1].rstrip("k"))
    assert 150 < bitrate < 2000


def test_target_bitrate_accounts_for_audio():
    bps = target_bitrate_kbps(100, 1400.0, audio_kbps=128)
    # 100 MiB over 1400s minus audio must be a sane video bitrate
    assert 200 < bps < 1200


def test_target_bitrate_falls_back_without_duration():
    assert target_bitrate_kbps(100, None) == 800


def test_unknown_profile_rejected():
    with pytest.raises(EncodeError):
        build_encode_args(Path("a"), Path("b"), profile="potato", info=make_info())


def test_every_output_is_progress_reported():
    for profile in ("original", "high", "balanced", "small"):
        args = build_encode_args(Path("a.mkv"), Path("b.mp4"), profile=profile, info=make_info(), out_format="mp4")
        if profile != "original":
            assert "pipe:1" in args


@pytest.mark.parametrize(
    "line,duration,expected",
    [
        ("out_time_us=700000000", 1400.0, 50.0),
        ("out_time_us=0", 1400.0, 0.0),
        ("out_time_us=999999999999", 1400.0, 100.0),
        ("out_time_us=700000000", None, None),
        ("progress=continue", 1400.0, None),
        ("out_time_us=not-a-number", 1400.0, None),
    ],
)
def test_parse_progress(line, duration, expected):
    assert parse_progress_line(line, duration) == expected


def test_profile_label():
    assert profile_label("balanced") == "BALANCED"
    assert profile_label("original") == "ORIGINAL"
