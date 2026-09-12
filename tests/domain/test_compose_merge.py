"""Host tests for the compose pass-fusion (Wave 5) and hook/flash wiring.

The grade+subtitles merge cuts a fully-toggled compose from 5 to 3 encode
generations without touching the load-bearing
Grade -> Subtitles -> Smart Cut -> Logo -> Banner -> Hook (flash) order:

* grade+subtitles: the grade chain rides as ``pre_vf`` on the subtitle burn —
  inside one filtergraph the colour transform still hits the source pixels
  BEFORE the glyphs are composited (identical semantics).

Hook is no longer an on-video overlay — turning it on renders the flash-intro
card instead (see hooks.add_intro_flash), always as its own last pass; logo
is always its own standalone on-video pass.

ffmpeg itself is exercised by the Docker integration suite; here the command
assembly and the compose_layers wiring are pinned with fakes.
"""
import asyncio
import os

from clippyme.domain import compose
from clippyme.domain import subtitles as subtitles_module
from clippyme.domain.logo import logo_filter_chain


# --- pure filter builders ----------------------------------------------------

def test_logo_filter_chain_clamps_and_geometry():
    chain, x, y = logo_filter_chain(1000, scale=0.9, opacity=1.7, margin=0.04,
                                    position="top-right")
    assert chain.startswith("scale=500:-1,")            # scale clamped to 0.5
    assert chain.endswith("colorchannelmixer=aa=1.000")  # opacity clamped to 1
    assert x == "main_w-overlay_w-40" and y == "40"      # margin 4% of 1000


# --- burn_subtitles pre_vf ----------------------------------------------------

def _capture_burn(monkeypatch, tmp_path):
    captured = {}

    class _Ok:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(subtitles_module.subprocess, "run",
                        lambda cmd, **k: captured.update(cmd=cmd) or _Ok())
    monkeypatch.setattr(subtitles_module, "effective_fonts_dir", lambda: str(tmp_path))
    return captured


def test_burn_subtitles_prepends_pre_vf(tmp_path, monkeypatch):
    captured = _capture_burn(monkeypatch, tmp_path)
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    subtitles_module.burn_subtitles("in.mp4", str(ass), "out.mp4",
                                    pre_vf="eq=contrast=1.06:saturation=1.1")
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert vf.startswith("eq=contrast=1.06:saturation=1.1,ass=")


def test_burn_subtitles_without_pre_vf_unchanged(tmp_path, monkeypatch):
    captured = _capture_burn(monkeypatch, tmp_path)
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    subtitles_module.burn_subtitles("in.mp4", str(ass), "out.mp4")
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert vf.startswith("ass=")


# --- compose_layers wiring ------------------------------------------------------

def _run_compose(tmp_path, monkeypatch, toggles, **kwargs):
    base = tmp_path / "clip.mp4"
    base.write_bytes(b"fake")
    calls = {"grade": 0, "logo": 0, "flash": 0, "flash_text": "unset", "pre_vf": "unset"}

    async def fake_grade(current_input, job_dir, clip_index, grade_params, files):
        calls["grade"] += 1
        return current_input

    async def fake_subs(current_input, job_dir, clip_index, metadata, clip_info,
                        subtitle_params, files, pre_vf=None, banner_active=False):
        calls["pre_vf"] = pre_vf
        out = os.path.join(job_dir, "subbed.mp4")
        with open(out, "wb") as f:
            f.write(b"s")
        files.append(out)
        return out

    async def fake_logo(current_input, job_dir, clip_index, logo_params, files):
        calls["logo"] += 1
        return current_input

    async def fake_flash(current_input, base_clip, job_dir, clip_index, hook_params, files,
                        metadata=None, clip_info=None):
        calls["flash"] += 1
        calls["flash_text"] = hook_params.get("text")
        out = os.path.join(job_dir, "flashed.mp4")
        with open(out, "wb") as f:
            f.write(b"f")
        files.append(out)
        return out

    monkeypatch.setattr(compose, "_apply_grade", fake_grade)
    monkeypatch.setattr(compose, "_apply_subtitles", fake_subs)
    monkeypatch.setattr(compose, "_apply_logo", fake_logo)
    monkeypatch.setattr(compose, "_apply_intro_flash", fake_flash)

    async def no_eval(*a, **k):
        return None

    monkeypatch.setattr(compose, "_self_eval", no_eval)

    result = asyncio.run(compose.compose_layers(
        base_clip=str(base), job_dir=str(tmp_path), clip_index=0,
        metadata={}, clip_info={}, toggles=toggles,
        hook_params=kwargs.get("hook_params", {}),
        subtitle_params=kwargs.get("subtitle_params", {}),
        logo_params=kwargs.get("logo_params"),
        grade_params=kwargs.get("grade_params"),
    ))
    return result, calls


def test_grade_fuses_into_subtitle_burn(tmp_path, monkeypatch):
    _, calls = _run_compose(
        tmp_path, monkeypatch,
        {"grade": True, "subtitles": True},
        grade_params={"preset": "warm_cinematic"},
    )
    assert calls["grade"] == 0, "standalone grade pass must be skipped when fused"
    assert calls["pre_vf"] and "eq=" in calls["pre_vf"]


def test_grade_alone_keeps_its_own_pass(tmp_path, monkeypatch):
    _, calls = _run_compose(
        tmp_path, monkeypatch,
        {"grade": True},
        grade_params={"preset": "warm_cinematic"},
    )
    assert calls["grade"] == 1


def test_unknown_grade_preset_falls_back_to_standalone_noop(tmp_path, monkeypatch):
    # build_grade_filter('') is empty → fusion impossible → the standalone
    # apply path runs (and no-ops), exactly like before the merge.
    _, calls = _run_compose(
        tmp_path, monkeypatch,
        {"grade": True, "subtitles": True},
        grade_params={"preset": "does_not_exist"},
    )
    assert calls["grade"] == 1
    assert calls["pre_vf"] is None


def test_logo_alone_keeps_its_own_pass(tmp_path, monkeypatch):
    logo_png = tmp_path / "logo.png"
    logo_png.write_bytes(b"\x89PNG")
    monkeypatch.setattr(compose, "LOGO_PATH", str(logo_png))
    _, calls = _run_compose(tmp_path, monkeypatch, {"logo": True},
                            logo_params={"size": "M"})
    assert calls["logo"] == 1
    assert calls["flash"] == 0


# --- hook renders ONLY as the flash-intro card, never on-video --------------

def test_hook_renders_as_flash_not_on_video_overlay(tmp_path, monkeypatch):
    _, calls = _run_compose(
        tmp_path, monkeypatch,
        {"hook": True},
        hook_params={"text": "WATCH"},
    )
    assert calls["flash"] == 1
    assert calls["flash_text"] == "WATCH"


def test_hook_and_logo_apply_independently(tmp_path, monkeypatch):
    # No more hook+logo fusion (that was an on-video-overlay-only optimisation)
    # — logo is always its own pass, hook always renders as its own flash pass.
    logo_png = tmp_path / "logo.png"
    logo_png.write_bytes(b"\x89PNG")
    monkeypatch.setattr(compose, "LOGO_PATH", str(logo_png))
    _, calls = _run_compose(
        tmp_path, monkeypatch,
        {"hook": True, "logo": True},
        hook_params={"text": "WATCH"},
        logo_params={"position": "top-right", "size": "M"},
    )
    assert calls["logo"] == 1
    assert calls["flash"] == 1
    assert calls["flash_text"] == "WATCH"


def test_hook_without_text_skips_flash_but_logo_still_applies(tmp_path, monkeypatch):
    logo_png = tmp_path / "logo.png"
    logo_png.write_bytes(b"\x89PNG")
    monkeypatch.setattr(compose, "LOGO_PATH", str(logo_png))
    _, calls = _run_compose(
        tmp_path, monkeypatch,
        {"hook": True, "logo": True},
        hook_params={"text": "   "},
        logo_params={"size": "M"},
    )
    assert calls["flash"] == 0, "hook layer must be skipped when text is blank"
    assert calls["logo"] == 1
