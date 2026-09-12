"""Host tests for hooks._speaker_time_windows — the pure diarization-to-
clip-relative-windows extraction the flash-intro avatar feature keys off."""
from clippyme.domain.hooks import _speaker_time_windows


def _transcript(segments):
    return {"segments": segments}


def test_no_speaker_field_returns_empty_for_fallback():
    transcript = _transcript([{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}])
    assert _speaker_time_windows(transcript, 0.0, 4.0) == {}


def test_groups_segments_by_speaker_and_shifts_clip_relative():
    transcript = _transcript([
        {"start": 10.0, "end": 12.0, "speaker": 0},
        {"start": 12.0, "end": 15.0, "speaker": 1},
        {"start": 15.0, "end": 17.0, "speaker": 0},
    ])
    windows = _speaker_time_windows(transcript, clip_start=10.0, clip_end=17.0)
    assert windows == {
        0: [(0.0, 2.0), (5.0, 7.0)],
        1: [(2.0, 5.0)],
    }


def test_segments_outside_clip_range_are_excluded():
    transcript = _transcript([
        {"start": 0.0, "end": 5.0, "speaker": 0},   # entirely before the clip
        {"start": 8.0, "end": 12.0, "speaker": 0},  # straddles clip_start=10
        {"start": 30.0, "end": 32.0, "speaker": 1},  # entirely after clip_end=20
    ])
    windows = _speaker_time_windows(transcript, clip_start=10.0, clip_end=20.0)
    assert windows == {0: [(0.0, 2.0)]}
    assert 1 not in windows


def test_none_speaker_on_some_segments_is_skipped_not_fatal():
    transcript = _transcript([
        {"start": 0.0, "end": 2.0, "speaker": 0},
        {"start": 2.0, "end": 4.0},  # no speaker tag on this one — ignored
    ])
    windows = _speaker_time_windows(transcript, 0.0, 4.0)
    assert windows == {0: [(0.0, 2.0)]}


def test_empty_transcript_returns_empty():
    assert _speaker_time_windows(None, 0.0, 10.0) == {}
    assert _speaker_time_windows({}, 0.0, 10.0) == {}
