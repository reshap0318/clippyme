"""Local laughter/audio-event detection via sherpa-onnx audio tagging.

ElevenLabs Scribe tags ``(laughter)``/``(applause)`` natively (see
``elevenlabs_transcribe.py``), which the Gemini viral-moment prompt reads as a
free signal. Deepgram and Whisper don't have that. This module fills the same
gap locally (no per-minute cloud cost) so Deepgram/Whisper transcripts get the
same ``audio_events`` signal ElevenLabs users already had.

Output shape matches ElevenLabs' extension exactly, so nothing downstream
needs to know which provider produced it::

    [{"text": "(laughter)", "start": float, "end": float}, ...]

Model: sherpa-onnx's zipformer-small audio-tagging (k2-fsa, AudioSet-trained,
~26MB int8 ONNX, CPU). It classifies a whole clip per call — no built-in
framewise/streaming output — so we slide a window over the audio ourselves and
merge contiguous positive windows into one event (VAD-style hangover merge),
the same idea as the waveform-silence merging in ``cut_ops.py``.

ponytail: if sherpa-onnx proves inaccurate for this, the documented fallback
is jrgillick/laughter-detection (Interspeech 2021, purpose-built laughter
CNN) — not on PyPI, needs vendoring the repo + its Switchboard checkpoint by
hand. Picked sherpa-onnx first because it installs clean (no conflicting
pins) and is already validated end-to-end against the bundled test wavs.

Env vars (all optional)
~~~~~~~~~~~~~~~~~~~~~~~~
- ``CLIPPYME_LAUGHTER_MODEL_DIR`` (default ``/opt/models/sherpa-onnx-audio-tagging``)
  — kept OUTSIDE /app deliberately: docker-compose bind-mounts the repo over
  /app at runtime, which would shadow anything baked there at build time
  (same reason auto-editor lives in /usr/local/bin, not /app).
- ``CLIPPYME_LAUGHTER_THRESHOLD`` (default 0.5) — min class probability/window
- ``CLIPPYME_LAUGHTER_WINDOW_SECONDS`` (default 2.0)
- ``CLIPPYME_LAUGHTER_HOP_SECONDS`` (default 1.0)
- ``CLIPPYME_LAUGHTER_MERGE_GAP_SECONDS`` (default 0.5) — bridges short dips
  inside one laugh so it doesn't fragment into multiple events.

Whether this module runs at all is decided by callers (``pipeline.main``,
gated on ``CLIPPYME_LAUGHTER_DETECTION``) — kept out of this module so it
stays a pure "detect or return []" unit.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("clippyme")

_DEFAULT_MODEL_DIR = "/opt/models/sherpa-onnx-audio-tagging"
# AudioSet classes that count as "laughter" for our purposes — the parent
# class plus its common sub-types.
_TARGET_CLASSES = {"Laughter", "Giggle", "Belly laugh", "Snicker", "Baby laughter"}


def _model_dir() -> str:
    return (os.getenv("CLIPPYME_LAUGHTER_MODEL_DIR") or _DEFAULT_MODEL_DIR).strip()


def detect_laughter_events(audio_path: str) -> list[dict]:
    """Detect laughter in ``audio_path``. Returns ``[]`` on any failure — this
    is best-effort enrichment, never allowed to break the pipeline."""
    try:
        import sherpa_onnx  # type: ignore
        import soundfile as sf  # type: ignore
    except ImportError as exc:
        logger.warning("Laughter detection skipped: %s not installed", getattr(exc, "name", exc))
        return []

    model_dir = _model_dir()
    model_path = os.path.join(model_dir, "model.int8.onnx")
    labels_path = os.path.join(model_dir, "class_labels_indices.csv")
    if not (os.path.isfile(model_path) and os.path.isfile(labels_path)):
        logger.warning("Laughter detection skipped: model not found at %s", model_dir)
        return []

    try:
        data, sr = sf.read(audio_path, dtype="float32", always_2d=False)
    except Exception as exc:  # noqa: BLE001 — bad/corrupt audio, don't crash the job
        logger.warning("Laughter detection skipped: failed to read audio (%s)", exc)
        return []
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.size == 0:
        return []

    try:
        tagger = sherpa_onnx.AudioTagging(
            sherpa_onnx.AudioTaggingConfig(
                model=sherpa_onnx.AudioTaggingModelConfig(
                    zipformer=sherpa_onnx.OfflineZipformerAudioTaggingModelConfig(model=model_path),
                    num_threads=2,
                    provider="cpu",  # deliberately CPU: leaves the GPU free for
                    # Whisper, and Deepgram's HTTP call is network-bound anyway.
                ),
                labels=labels_path,
                top_k=5,
            )
        )
    except Exception as exc:  # noqa: BLE001 — corrupt/missing model files
        logger.warning("Laughter detection skipped: failed to load model (%s)", exc)
        return []

    window_s = float(os.getenv("CLIPPYME_LAUGHTER_WINDOW_SECONDS") or 2.0)
    hop_s = float(os.getenv("CLIPPYME_LAUGHTER_HOP_SECONDS") or 1.0)
    threshold = float(os.getenv("CLIPPYME_LAUGHTER_THRESHOLD") or 0.5)
    merge_gap = float(os.getenv("CLIPPYME_LAUGHTER_MERGE_GAP_SECONDS") or 0.5)

    window_n = max(1, int(window_s * sr))
    hop_n = max(1, int(hop_s * sr))
    total_n = len(data)

    hits: list[tuple[float, float]] = []
    pos = 0
    while pos < total_n:
        chunk = data[pos:pos + window_n]
        if len(chunk) < sr * 0.2:  # tail too short to classify meaningfully
            break
        stream = tagger.create_stream()
        stream.accept_waveform(sample_rate=sr, waveform=chunk)
        try:
            events = tagger.compute(stream)
        except Exception as exc:  # noqa: BLE001 — skip this window, keep going
            logger.warning("Laughter detection: window at %.1fs failed (%s)", pos / sr, exc)
            pos += hop_n
            continue
        best = max((e.prob for e in events if e.name in _TARGET_CLASSES), default=0.0)
        if best >= threshold:
            hits.append((pos / sr, (pos + len(chunk)) / sr))
        pos += hop_n

    if not hits:
        return []

    merged: list[list[float]] = [list(hits[0])]
    for start, end in hits[1:]:
        if start - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    return [{"text": "(laughter)", "start": s, "end": e} for s, e in merged]


def weave_events_into_text(transcript: dict, events: list[dict]) -> str:
    """Re-flatten ``transcript['segments'][].words[]`` + ``events`` into one
    string ordered by timestamp, so the Gemini prompt sees
    ``…funny bit (laughter) and then…`` — same idea as ElevenLabs'
    ``_weave_text``, adapted to our segments/words transcript shape instead of
    a flat Scribe token stream. Falls back to the existing ``text`` when
    there's nothing to weave."""
    if not events:
        return transcript.get("text", "")
    tokens: list[tuple[float, str]] = []
    for seg in transcript.get("segments") or []:
        for w in seg.get("words") or []:
            tokens.append((float(w.get("start", 0.0)), str(w.get("word", ""))))
    tokens += [(float(e["start"]), str(e["text"])) for e in events]
    tokens.sort(key=lambda t: t[0])
    woven = " ".join(tok for _, tok in tokens if tok).strip()
    return woven or transcript.get("text", "")
