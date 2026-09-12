"""Integration smoke tests for the NEW ffmpeg render paths added across the
8 improvements. Pure helpers are host-tested elsewhere; these prove the actual
ffmpeg invocations are VALID and produce a playable file (real ffmpeg needed →
`integration`-marked, runs in Docker).

Covers:
  #1  smartcut afade segment render (audio fades at concat boundaries)
  #4  grade.apply_grade colour pass
  #5  hooks.add_intro_flash (hook renders ONLY as the flash-intro card now,
      never an on-video overlay — see hooks.py / compose.py)
"""
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def _has_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False


def _make_clip(path, dur=2):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=320x240:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _streams(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path]
    ).decode()
    return out.split()


@pytest.fixture()
def clip(tmp_path):
    if not _has_ffmpeg():
        pytest.skip("ffmpeg not available")
    p = str(tmp_path / "src.mp4")
    _make_clip(p)
    assert os.path.getsize(p) > 0
    return p


def test_grade_renders(clip, tmp_path):
    from clippyme.domain.grade import apply_grade

    out = str(tmp_path / "graded.mp4")
    assert apply_grade(clip, out, "warm_cinematic") is True
    assert os.path.getsize(out) > 0
    assert "video" in _streams(out)


def test_grade_none_is_noop(clip, tmp_path):
    from clippyme.domain.grade import apply_grade

    out = str(tmp_path / "none.mp4")
    assert apply_grade(clip, out, "none") is False
    assert not os.path.exists(out)


def _duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path]
    ).decode().strip()
    return float(out)


def test_intro_flash_prepends_a_short_card(clip, tmp_path):
    from clippyme.domain.hooks import add_intro_flash

    out = str(tmp_path / "flashed.mp4")
    ok = add_intro_flash(clip, "WAIT FOR IT", out, duration=0.5)
    assert ok is True
    assert os.path.getsize(out) > 0
    s = _streams(out)
    assert "video" in s and "audio" in s
    # Original clip fixture is 2s; the flash adds ~0.5s on top.
    assert _duration(out) == pytest.approx(2.5, abs=0.3)


def test_intro_flash_uses_frame_source_not_video_path(clip, tmp_path):
    """Regression: the flash's background/avatar frame must come from
    ``frame_source_path``, never ``video_path`` — extracting frame 0 from an
    already-composed clip (hook/subs/logo burned in) would bake those
    overlays right into the blurred background and any face crop.
    """
    from clippyme.domain.hooks import add_intro_flash

    # A "composed" clip whose frame 0 is visually distinct (solid red) from
    # the clean `clip` fixture (grey testsrc) — easy to tell apart by pixel.
    composed = str(tmp_path / "composed.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:size=320x240:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", composed,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    out = str(tmp_path / "flashed_clean_source.mp4")
    ok = add_intro_flash(composed, "HOOK", out, duration=0.5, frame_source_path=clip)
    assert ok is True

    frame_png = str(tmp_path / "flash_frame.png")
    subprocess.run(
        ["ffmpeg", "-y", "-i", out, "-vf", r"select=eq(n\,2)", "-vframes", "1", frame_png],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    from PIL import Image
    r, g, b = Image.open(frame_png).convert("RGB").getpixel((5, 5))
    assert not (r > 200 and g < 60 and b < 60), (
        "flash background leaked from `composed` (red) instead of frame_source_path"
    )


def test_smartcut_afade_segments_render(clip, tmp_path):
    from clippyme.domain.smartcut import _render_with_ffmpeg

    out = str(tmp_path / "cut.mp4")
    # Two kept segments → one internal concat boundary that must fade, not pop.
    ok = _render_with_ffmpeg(clip, [(0.0, 0.8), (1.2, 2.0)], out)
    assert ok is True
    assert os.path.getsize(out) > 0
    assert "audio" in _streams(out)


def test_burn_subtitles_with_grade_prevf_renders(clip, tmp_path):
    """Wave-5 fusion: grade chain rides as pre_vf on the subtitle burn."""
    from clippyme.domain.grade import build_grade_filter
    from clippyme.domain.subtitles import burn_subtitles

    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,500\nHello grade\n",
                   encoding="utf-8")
    out = str(tmp_path / "graded_subs.mp4")
    ok = burn_subtitles(clip, str(srt), out,
                        pre_vf=build_grade_filter("warm_cinematic"))
    assert ok is True
    assert os.path.getsize(out) > 0
    assert "video" in _streams(out)
