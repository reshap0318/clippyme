"""Host tests for hooks._pick_speaker_avatars — per-speaker avatar picking
plus the duplicate-face dedup that stops two diarized speaker IDs landing on
the same physical face (e.g. a camera that keeps one dominant person framed
while someone else talks)."""
import os

from PIL import Image

from clippyme.domain import hooks


def test_same_face_across_speakers_is_deduped_not_repeated(tmp_path, monkeypatch):
    def fake_extract(source_path, ts, out_path):
        # Every sampled instant returns the exact same frame/face — simulates
        # a camera that never leaves the same dominant person on screen.
        Image.new("RGB", (200, 200), (200, 30, 30)).save(out_path)

    def fake_detect(path, w, h):
        return [{"score": 100.0, "box": (50, 50, 100, 100)}]

    monkeypatch.setattr(hooks, "_extract_frame_at", fake_extract)
    monkeypatch.setattr(hooks, "_detect_flash_faces", fake_detect)

    speaker_windows = {0: [(0.0, 5.0)], 1: [(6.0, 11.0)]}
    bg_path, avatar_sources, scratch = hooks._pick_speaker_avatars(
        "source.mp4", 1080, 1920, speaker_windows,
    )
    try:
        assert len(avatar_sources) == 1  # speaker 1 skipped: same face as speaker 0
    finally:
        for p in ({bg_path} | set(scratch)):
            if p and os.path.exists(p):
                os.remove(p)


def test_different_faces_across_speakers_both_kept(tmp_path, monkeypatch):
    def fake_extract(source_path, ts, out_path):
        # Speaker 0's window is entirely < 5.5s, speaker 1's entirely after —
        # genuinely different people/frames, distinguishable by color here.
        color = (200, 30, 30) if ts < 5.5 else (30, 30, 200)
        Image.new("RGB", (200, 200), color).save(out_path)

    def fake_detect(path, w, h):
        return [{"score": 100.0, "box": (50, 50, 100, 100)}]

    monkeypatch.setattr(hooks, "_extract_frame_at", fake_extract)
    monkeypatch.setattr(hooks, "_detect_flash_faces", fake_detect)

    speaker_windows = {0: [(0.0, 5.0)], 1: [(6.0, 11.0)]}
    bg_path, avatar_sources, scratch = hooks._pick_speaker_avatars(
        "source.mp4", 1080, 1920, speaker_windows,
    )
    try:
        assert len(avatar_sources) == 2
    finally:
        for p in ({bg_path} | set(scratch)):
            if p and os.path.exists(p):
                os.remove(p)


def test_signature_ignores_background_outside_tight_crop():
    # Two crops with the SAME face-box color but different surrounding
    # background must still read as the same signature — the tight (pad=1.1)
    # crop used for the signature should mostly exclude the background that
    # the wider avatar-render crop (pad=1.8) would include.
    img_a = Image.new("RGB", (200, 200), (10, 200, 10))
    box = (60, 60, 80, 80)
    for x in range(60, 140):
        for y in range(60, 140):
            img_a.putpixel((x, y), (200, 30, 30))
    img_b = img_a.copy()
    # Paint the background (outside the face box) a totally different color.
    for x in range(0, 200):
        for y in range(0, 200):
            if not (60 <= x < 140 and 60 <= y < 140):
                img_b.putpixel((x, y), (10, 10, 200))

    sig_a = hooks._face_signature(img_a, box)
    sig_b = hooks._face_signature(img_b, box)
    assert hooks._signatures_match(sig_a, sig_b)


def test_signature_distinguishes_different_face_colors():
    box = (60, 60, 80, 80)
    img_red = Image.new("RGB", (200, 200), (0, 0, 0))
    img_blue = Image.new("RGB", (200, 200), (0, 0, 0))
    for x in range(60, 140):
        for y in range(60, 140):
            img_red.putpixel((x, y), (200, 30, 30))
            img_blue.putpixel((x, y), (30, 30, 200))

    sig_red = hooks._face_signature(img_red, box)
    sig_blue = hooks._face_signature(img_blue, box)
    assert not hooks._signatures_match(sig_red, sig_blue)


def test_flash_max_avatars_env_config(monkeypatch):
    monkeypatch.delenv("CLIPPYME_FLASH_MAX_AVATARS", raising=False)
    assert hooks._flash_max_avatars() == 3
    monkeypatch.setenv("CLIPPYME_FLASH_MAX_AVATARS", "10")
    assert hooks._flash_max_avatars() == 4  # clamped to the hard ceiling
    monkeypatch.setenv("CLIPPYME_FLASH_MAX_AVATARS", "0")
    assert hooks._flash_max_avatars() == 1  # clamped to the floor
    monkeypatch.setenv("CLIPPYME_FLASH_MAX_AVATARS", "not-a-number")
    assert hooks._flash_max_avatars() == 3  # bad value falls back to default


def test_flash_max_windows_env_config(monkeypatch):
    monkeypatch.delenv("CLIPPYME_FLASH_MAX_WINDOWS", raising=False)
    assert hooks._flash_max_windows() == 5
    monkeypatch.setenv("CLIPPYME_FLASH_MAX_WINDOWS", "20")
    assert hooks._flash_max_windows() == 10  # clamped to the hard ceiling
    monkeypatch.setenv("CLIPPYME_FLASH_MAX_WINDOWS", "0")
    assert hooks._flash_max_windows() == 1


def test_seed_rotates_which_window_is_tried_first(tmp_path, monkeypatch):
    calls = []

    def fake_extract(source_path, ts, out_path):
        calls.append(ts)
        Image.new("RGB", (200, 200), (200, 30, 30)).save(out_path)

    def fake_detect(path, w, h):
        return [{"score": 100.0, "box": (50, 50, 100, 100)}]

    monkeypatch.setattr(hooks, "_extract_frame_at", fake_extract)
    monkeypatch.setattr(hooks, "_detect_flash_faces", fake_detect)

    windows = {0: [(0.0, 10.0), (20.0, 27.0), (40.0, 45.0)]}

    for seed, expected_range in [(0, (0.0, 10.0)), (1, (20.0, 27.0)), (2, (40.0, 45.0))]:
        calls.clear()
        bg_path, avatar_sources, scratch = hooks._pick_speaker_avatars(
            "source.mp4", 1080, 1920, windows, seed=seed,
        )
        try:
            assert len(avatar_sources) == 1
            first_ts = calls[0]
            assert expected_range[0] <= first_ts <= expected_range[1], (seed, first_ts)
        finally:
            for p in ({bg_path} | set(scratch)):
                if p and os.path.exists(p):
                    os.remove(p)


def test_max_avatars_param_caps_speaker_count(tmp_path, monkeypatch):
    def fake_extract(source_path, ts, out_path):
        color = (200, 30, 30) if ts < 5.5 else (30, 30, 200)
        Image.new("RGB", (200, 200), color).save(out_path)

    def fake_detect(path, w, h):
        return [{"score": 100.0, "box": (50, 50, 100, 100)}]

    monkeypatch.setattr(hooks, "_extract_frame_at", fake_extract)
    monkeypatch.setattr(hooks, "_detect_flash_faces", fake_detect)

    speaker_windows = {0: [(0.0, 5.0)], 1: [(6.0, 11.0)]}
    bg_path, avatar_sources, scratch = hooks._pick_speaker_avatars(
        "source.mp4", 1080, 1920, speaker_windows, max_avatars=1,
    )
    try:
        assert len(avatar_sources) == 1  # capped even though speaker 1 has a distinct face
    finally:
        for p in ({bg_path} | set(scratch)):
            if p and os.path.exists(p):
                os.remove(p)


def test_speaker_skipped_when_every_sampled_window_has_no_face(tmp_path, monkeypatch):
    def fake_extract(source_path, ts, out_path):
        Image.new("RGB", (200, 200), (10, 10, 10)).save(out_path)

    def fake_detect(path, w, h):
        return []  # nobody detected anywhere

    monkeypatch.setattr(hooks, "_extract_frame_at", fake_extract)
    monkeypatch.setattr(hooks, "_detect_flash_faces", fake_detect)

    speaker_windows = {0: [(0.0, 5.0)]}
    bg_path, avatar_sources, scratch = hooks._pick_speaker_avatars(
        "source.mp4", 1080, 1920, speaker_windows,
    )
    try:
        assert avatar_sources == []
        assert bg_path is not None  # background fallback still resolves
    finally:
        for p in ({bg_path} | set(scratch)):
            if p and os.path.exists(p):
                os.remove(p)
