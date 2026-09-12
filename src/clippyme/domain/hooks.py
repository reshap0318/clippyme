import contextlib
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from clippyme.domain.encode import ffmpeg_timeout, x264_video_args

logger = logging.getLogger(__name__)

FONT_URL = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSerif/NotoSerif-Bold.ttf"

# Hard cap for runtime font downloads — defends against a hostile/compromised
# mirror serving a multi-GB payload (or a decompression bomb) into memory.
_FONT_MAX_BYTES = 25 * 1024 * 1024
_FONT_HTTP_TIMEOUT = 30

_FONT_ALLOWED_HOSTS = frozenset({"github.com", "raw.githubusercontent.com"})
_FONT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
_FONT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")


def _allowed_font_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in _FONT_ALLOWED_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
    )


class _SafeFontRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_font_url(newurl):
            raise RuntimeError("font download redirected to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_FONT_OPENER = urllib.request.build_opener(_SafeFontRedirectHandler)


def runtime_font_download_enabled() -> bool:
    """Runtime font network access is opt-in; bundled/user fonts remain available."""
    return os.environ.get("CLIPPYME_RUNTIME_FONT_DOWNLOAD", "0") == "1"


def _is_valid_font_file(path: str) -> bool:
    try:
        with open(path, "rb") as file:
            head = file.read(4)
        return any(head.startswith(magic) for magic in _FONT_MAGICS)
    except OSError:
        return False


def _download_capped(req, out_path):
    """Atomically stream a trusted font request to disk with a hard cap."""
    if not _allowed_font_url(req.full_url):
        raise RuntimeError("untrusted font download URL")
    directory = os.path.dirname(out_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".font-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as out_file:
            with _FONT_OPENER.open(req, timeout=_FONT_HTTP_TIMEOUT) as response:  # nosec B310: HTTPS host and every redirect are allowlisted
                total = 0
                head = b""
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _FONT_MAX_BYTES:
                        raise RuntimeError("font download exceeded size cap")
                    if len(head) < 4:
                        head = (head + chunk)[:4]
                    out_file.write(chunk)
                out_file.flush()
                os.fsync(out_file.fileno())
        if not any(head.startswith(magic) for magic in _FONT_MAGICS):
            raise RuntimeError("downloaded payload is not a TrueType/OpenType font")
        os.replace(tmp_path, out_path)
        tmp_path = None
    finally:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)


# Resolve bundled fonts dir by walking up from __file__ (→ repo-root/fonts).
# A bare CWD-relative "fonts" broke for any caller not launched from the
# repo root (reframe subprocess, tests, ad-hoc CLI from /tmp). Env override
# lets operators point at a different install prefix.
_REPO_ROOT_FROM_HERE = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
FONT_DIR = os.environ.get("CLIPPYME_FONTS_DIR") or os.path.join(_REPO_ROOT_FROM_HERE, "fonts")
if not os.path.isdir(FONT_DIR):
    _cwd_fallback = os.path.abspath("fonts")
    if os.path.isdir(_cwd_fallback):
        FONT_DIR = _cwd_fallback
FONT_PATH = os.path.join(FONT_DIR, "NotoSerif-Bold.ttf")


def download_font_if_needed():
    """Downloads a serif font for the hook text if not present."""
    os.makedirs(FONT_DIR, exist_ok=True)
    if not _is_valid_font_file(FONT_PATH):
        if not runtime_font_download_enabled():
            logger.warning(
                "Bundled hook font is missing/invalid; runtime download is disabled "
                "(set CLIPPYME_RUNTIME_FONT_DOWNLOAD=1 to enable)"
            )
            return
        logger.info("⬇️ Downloading font from %s...", FONT_URL)
        try:
            req = urllib.request.Request(FONT_URL, headers={"User-Agent": "Mozilla/5.0"})
            _download_capped(req, FONT_PATH)
            logger.info("✅ Font downloaded.")
        except Exception as e:
            logger.error("❌ Failed to download font: %s", e)


EMOJI_FONT_URL = "https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf"
EMOJI_FONT_PATH = os.path.join(FONT_DIR, "NotoColorEmoji.ttf")


def download_emoji_font_if_needed():
    """Downloads the Noto Color Emoji font if not present."""
    os.makedirs(FONT_DIR, exist_ok=True)
    if not _is_valid_font_file(EMOJI_FONT_PATH):
        if not runtime_font_download_enabled():
            logger.debug("Emoji font missing; runtime font download is disabled")
            return
        logger.info("Downloading emoji font...")
        try:
            req = urllib.request.Request(EMOJI_FONT_URL, headers={"User-Agent": "Mozilla/5.0"})
            _download_capped(req, EMOJI_FONT_PATH)
            logger.info("Emoji font downloaded.")
        except Exception as e:
            logger.error("Failed to download emoji font: %s", e)


def has_emoji(text):
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF"
        "\U0000FE00-\U0000FE0F\U0000200D]+"
    )
    return bool(emoji_pattern.search(text))


_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex_to_rgba(hex_str, alpha=255, default=(0, 0, 0)):
    """#RRGGBB → (r, g, b, alpha), with a safe fallback."""
    if isinstance(hex_str, str) and _HEX_RE.match(hex_str):
        h = hex_str.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha))
    return (*default, int(alpha))


def _resolve_hook_font_path(font_name):
    """Resolve a safe font *name* inside the bundled/user font directories.

    Absolute paths and traversal components are intentionally rejected. Overlay
    parameters are user-controlled and must never become an arbitrary filesystem
    existence oracle or allow loading a font outside the configured directories.
    """
    safe_name = str(font_name or "").strip()
    if safe_name and _FONT_NAME_RE.fullmatch(safe_name):
        dirs = [FONT_DIR]
        try:
            from clippyme.domain.subtitles import USER_FONTS_DIR
            dirs.append(USER_FONTS_DIR)
        except Exception:
            pass
        for directory in dirs:
            root = os.path.abspath(directory)
            for ext in (".ttf", ".otf", ".ttc"):
                candidate = os.path.abspath(os.path.join(root, f"{safe_name}{ext}"))
                if os.path.commonpath((root, candidate)) == root and os.path.isfile(candidate):
                    return candidate
    download_font_if_needed()
    return FONT_PATH


# Instagram-Stories-style defaults: bannerless white Anton with a thin black
# outline (the bannerless path auto-adds a soft drop shadow for legibility).
HOOK_STYLE_DEFAULTS = {
    "text_color": "#FFFFFF",
    "bg_enabled": False,
    "bg_color": "#FFFFFF",
    "bg_opacity": 0.94,
    "corner_radius": 20,
    "outline_color": "#000000",
    "outline_width": 4,
    "font": "Anton-Regular",
    "shadow": None,
}


def _text_width(draw, value, font, stroke_width):
    bbox = draw.textbbox((0, 0), value, font=font, stroke_width=stroke_width)
    return bbox[2] - bbox[0]


def _split_overlong_word(draw, word, font, max_width, stroke_width):
    """Split a no-whitespace token so it cannot create a huge Pillow canvas."""
    if _text_width(draw, word, font, stroke_width) <= max_width:
        return [word]
    chunks = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_width(draw, candidate, font, stroke_width) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [word]


def create_hook_image(text, target_width, output_image_path="hook_overlay.png",
                      font_scale=1.0, style=None):
    """Render a hook text overlay PNG (transparent canvas)."""
    s = {**HOOK_STYLE_DEFAULTS, **(style or {})}
    bg_enabled = bool(s["bg_enabled"])
    bg_opacity = max(0.0, min(1.0, float(s["bg_opacity"])))
    text_rgba = _hex_to_rgba(s["text_color"], 255, default=(0, 0, 0))
    bg_rgba = _hex_to_rgba(s["bg_color"], int(round(bg_opacity * 255)), default=(255, 255, 255))
    outline_w = max(0, min(20, int(s["outline_width"])))
    outline_rgba = _hex_to_rgba(s["outline_color"], 255, default=(0, 0, 0))
    corner_radius = max(0, min(80, int(s["corner_radius"])))
    shadow = (not bg_enabled) if s["shadow"] is None else bool(s["shadow"])

    target_width = max(64, min(int(target_width), 8192))
    padding_x = 30 if bg_enabled else 12
    padding_y = 25 if bg_enabled else 10
    line_spacing = 20

    base_font_size = int(target_width * 0.05)
    font_size = max(8, min(512, int(base_font_size * float(font_scale))))

    font_path = _resolve_hook_font_path(s["font"])
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except Exception:
            font = ImageFont.load_default()

    dummy_img = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy_img)
    max_text_width = max(1, target_width - (2 * padding_x))

    lines = []
    for paragraph in str(text or "").split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current_line = []
        words = []
        for word in paragraph.split():
            words.extend(_split_overlong_word(draw, word, font, max_text_width, outline_w))
        for word in words:
            test_line = " ".join(current_line + [word])
            if _text_width(draw, test_line, font, outline_w) <= max_text_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))

    if not lines:
        lines = [""]

    max_line_width = 0
    text_heights = []
    for line in lines:
        if not line:
            text_heights.append(font_size)
            continue
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=outline_w)
        max_line_width = max(max_line_width, bbox[2] - bbox[0])
        text_heights.append(bbox[3] - bbox[1])

    min_box = int(target_width * 0.3) if bg_enabled else max_line_width
    box_width = min(target_width, max(max_line_width + 2 * padding_x, min_box))
    total_text_height = sum(text_heights) + (len(text_heights) - 1) * line_spacing
    box_height = total_text_height + 2 * padding_y

    margin = 20
    canvas_w = box_width + 2 * margin
    canvas_h = box_height + 2 * margin
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    if shadow:
        shadow_offset = (4, 4)
        shadow_draw = ImageDraw.Draw(img)
        if bg_enabled:
            shadow_draw.rounded_rectangle(
                [(margin + shadow_offset[0], margin + shadow_offset[1]),
                 (margin + box_width + shadow_offset[0], margin + box_height + shadow_offset[1])],
                radius=corner_radius, fill=(0, 0, 0, 110))
        else:
            cy = margin + padding_y - 2
            for i, line in enumerate(lines):
                if line:
                    lw = _text_width(shadow_draw, line, font, outline_w)
                    lx = margin + (box_width - lw) // 2
                    shadow_draw.text(
                        (lx + shadow_offset[0], cy + shadow_offset[1]),
                        line,
                        font=font,
                        fill=(0, 0, 0, 150),
                        stroke_width=outline_w,
                    )
                cy += text_heights[i] + line_spacing
        img = img.filter(ImageFilter.GaussianBlur(5))

    draw_final = ImageDraw.Draw(img)
    if bg_enabled:
        radius = min(corner_radius, box_height // 2, box_width // 2)
        draw_final.rounded_rectangle(
            [(margin, margin), (margin + box_width, margin + box_height)],
            radius=radius, fill=bg_rgba)

    emoji_font = None
    if any(has_emoji(line) for line in lines if line):
        download_emoji_font_if_needed()
        try:
            emoji_font = ImageFont.truetype(EMOJI_FONT_PATH, font_size)
        except Exception:
            emoji_font = None

    current_y = margin + padding_y - 2
    for i, line in enumerate(lines):
        if not line:
            current_y += font_size + line_spacing
            continue
        render_font = emoji_font if emoji_font and has_emoji(line) else font
        bbox = draw_final.textbbox((0, 0), line, font=render_font)
        line_w = bbox[2] - bbox[0]
        x = margin + (box_width - line_w) // 2

        if render_font is emoji_font:
            draw_final.text((x, current_y), line, font=render_font, embedded_color=True)
        elif outline_w > 0:
            draw_final.text(
                (x, current_y), line, font=render_font, fill=text_rgba,
                stroke_width=outline_w, stroke_fill=outline_rgba)
        else:
            draw_final.text((x, current_y), line, font=render_font, fill=text_rgba)

        current_y += text_heights[i] + line_spacing

    img.save(output_image_path)
    return output_image_path, canvas_w, canvas_h

_FLASH_MAX_FACES_CEILING = 4  # hard ceiling regardless of env config
_FLASH_MIN_FACE_AREA_FRAC = 0.01  # a detected face smaller than this % of the
                                  # frame is background clutter, not a subject


def _flash_max_avatars() -> int:
    """``CLIPPYME_FLASH_MAX_AVATARS`` (default 3), clamped to
    ``[1, _FLASH_MAX_FACES_CEILING]``. Fewer avatars in the flash card means
    less exposure to the dedup's known false-negative rate (see
    ``_signatures_match``) — a smaller cast is less likely to trip it."""
    try:
        value = int(os.environ.get("CLIPPYME_FLASH_MAX_AVATARS", "3"))
    except ValueError:
        value = 3
    return max(1, min(_FLASH_MAX_FACES_CEILING, value))
# Sampled at these fractions of the clip's duration when hunting for the
# frame with the most people on screen at once — not just t=0, which might
# land before the rest of the cast has appeared on camera.
_FLASH_FRAME_SAMPLE_FRACTIONS = (0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9)


def _clip_duration(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            timeout=30,
        ).decode().strip()
        return float(out)
    except Exception:
        return 0.0


def _extract_frame_at(source_path, timestamp, out_path):
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{max(0.0, timestamp):.3f}", "-i", source_path,
         "-vframes", "1", out_path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=ffmpeg_timeout(),
    )


def _pick_best_flash_frame(source_path, video_width, video_height):
    """Sample a handful of frames spread across the WHOLE clip and keep
    whichever has the most qualifying faces — a cheap proxy for "the moment
    with the most of the cast on screen at once", without needing real face
    re-identification across frames (matching the SAME person between two
    frames needs face embeddings, not just detection — out of scope for a
    sub-second flash card).

    Returns ``(frame_path, faces)`` for the winner — ``frame_path`` is a temp
    PNG the caller owns and must remove. Falls back to a plain frame-0 grab
    (``faces`` possibly empty) on any failure, so there is always a usable
    frame.
    """
    duration = _clip_duration(source_path)
    timestamps = [0.0]
    if duration > 0:
        timestamps = sorted({
            round(duration * f, 3) for f in _FLASH_FRAME_SAMPLE_FRACTIONS
            if duration * f < duration
        }) or [0.0]

    best_path, best_faces = None, []
    candidate_paths = []
    for i, ts in enumerate(timestamps):
        fd, candidate_path = tempfile.mkstemp(prefix=f"clippyme-flashcand{i}-", suffix=".png")
        os.close(fd)
        candidate_paths.append(candidate_path)
        try:
            _extract_frame_at(source_path, ts, candidate_path)
        except Exception:
            continue
        faces = _detect_flash_faces(candidate_path, video_width, video_height)
        if best_path is None or len(faces) > len(best_faces):
            best_path, best_faces = candidate_path, faces

    if best_path is None:
        fd, best_path = tempfile.mkstemp(prefix="clippyme-flashcand-fallback-", suffix=".png")
        os.close(fd)
        with contextlib.suppress(Exception):
            _extract_frame_at(source_path, 0.0, best_path)
        best_faces = []

    for p in candidate_paths:
        if p != best_path:
            with contextlib.suppress(OSError):
                os.remove(p)
    return best_path, best_faces


def _detect_flash_faces(frame_path, video_width, video_height):
    """Up to ``_flash_max_avatars()`` largest faces in the flash background
    frame, sorted left-to-right for a natural reading order.

    Reuses the same lightweight MediaPipe detector the reframe pipeline uses
    (cached singleton — no extra model load beyond the first call anywhere in
    the process). Returns an empty list on any failure or when nothing clears
    the size floor, so the caller falls back to the plain blur+text card.

    ``detect_face_candidates``'s "score" is just box area (w*h), not a
    detection confidence — so a big-enough false positive (a guitar
    headstock's tuning pegs read as eyes/mouth at the right scale, in
    practice) can pass the area floor untouched. FaceMesh (the same model
    the reframe pipeline uses for MAR) needs actual facial structure to lock
    onto — running it as a cheap second opinion on each surviving candidate
    rejects non-face shapes the plain area filter can't. This is the ONLY
    FaceMesh use in this module — the avatar-dedup signature (see
    ``_face_signature``) is a plain color histogram, not a face-identity model.
    """
    try:
        import cv2

        from clippyme.pipeline.reframe_detect import _get_face_mesh, detect_face_candidates
        frame = cv2.imread(frame_path)
        if frame is None:
            return []
        min_area = _FLASH_MIN_FACE_AREA_FRAC * video_width * video_height
        candidates = [c for c in detect_face_candidates(frame) if c["score"] >= min_area]
        candidates.sort(key=lambda c: c["score"], reverse=True)
        candidates = candidates[:_flash_max_avatars()]

        verified = []
        for c in candidates:
            x, y, w, h = c["box"]
            height, width = frame.shape[:2]
            pad = int(max(w, h) * 0.2)
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(width, x + w + pad), min(height, y + h + pad)
            if x2 - x1 < 20 or y2 - y1 < 20:
                continue
            roi = frame[y1:y2, x1:x2]
            rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            if _get_face_mesh().process(rgb).multi_face_landmarks:
                verified.append(c)

        verified.sort(key=lambda c: c["box"][0])
        return verified
    except Exception:
        return []


def _speaker_time_windows(transcript, clip_start, clip_end):
    """``{speaker_id: [(start, end), ...]}`` clip-relative time windows for
    every speaker with at least one transcript segment inside
    ``[clip_start, clip_end]`` — segments carry a majority-voted ``speaker``
    int when the ASR provider ran diarization (Deepgram/ElevenLabs; absent
    on the plain Whisper path or when diarization was off).

    Returns ``{}`` when the transcript has NO speaker labels at all, so the
    caller can tell "nobody talks in this clip" (impossible — a clip is
    built from transcript words) apart from "this transcript was never
    diarized", which is the real fallback signal.
    """
    windows: dict[int, list[tuple[float, float]]] = {}
    has_speaker_field = False
    for seg in (transcript or {}).get("segments", []) or []:
        speaker = seg.get("speaker")
        if speaker is None:
            continue
        has_speaker_field = True
        s, e = seg.get("start"), seg.get("end")
        if s is None or e is None:
            continue
        s, e = float(s), float(e)
        if e <= clip_start or s >= clip_end:
            continue
        s = max(s, clip_start) - clip_start
        e = min(e, clip_end) - clip_start
        if e <= s:
            continue
        windows.setdefault(int(speaker), []).append((s, e))
    return windows if has_speaker_field else {}


def _crop_face_square(source_img, box, pad=1.8):
    """Padded square crop around a face ``box`` from ``source_img`` (PIL, RGB) —
    shared by the circular-avatar render (generous ``pad``, for a nice-looking
    chip) and the dedup signature (tight ``pad``, to keep background out of
    the identity comparison)."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    side = min(max(w, h) * pad, source_img.width, source_img.height)
    left = max(0, min(cx - side / 2, source_img.width - side))
    top = max(0, min(cy - side / 2, source_img.height - side))
    return source_img.crop((int(left), int(top), int(left + side), int(top + side)))


_SIG_MATCH_THRESHOLD = 0.12


def _face_signature(source_img, box):
    """Coarse HSV color-histogram signature for "is this the same face crop
    as that other one" — used to stop two diarized speaker IDs (or a camera
    that keeps one dominant person framed while someone else talks) landing
    on the same physical person's photo twice.

    A TIGHT crop (``pad=1.1``, vs. the avatar render's 1.8) keeps background
    pixels from diluting the comparison — the earlier grayscale-thumbnail
    version cropped the same generous region used for rendering, so two
    different people in front of a similar dark background could read as
    "close enough". Not real face recognition (no ID-invariant embedding
    model in this pipeline), so it can still miss on a genuine close
    lookalike or an extreme lighting/angle swing — just meaningfully harder
    to fool than a plain grayscale hash.
    """
    tight = _crop_face_square(source_img, box, pad=1.1)
    hsv = tight.convert("HSV").resize((24, 24), Image.LANCZOS)
    raw = hsv.histogram()  # 256 bins each for H, S, V concatenated (768 total)
    # Coarsen 256 -> 16 bins per channel: less sensitive to small lighting/
    # noise shifts, mainly captures the broad color/tone distribution.
    coarse = [sum(raw[c * 256 + b * 16: c * 256 + b * 16 + 16]) for c in range(3) for b in range(16)]
    total = sum(coarse) or 1
    return [v / total for v in coarse]


def _signatures_match(a, b, threshold=_SIG_MATCH_THRESHOLD):
    # L1 distance between two normalized histograms, halved so the range is
    # a clean [0, 1] (each bin's difference is double-counted otherwise).
    return (sum(abs(x - y) for x, y in zip(a, b)) / 2) < threshold


_FLASH_MAX_WINDOWS_CEILING = 10  # hard ceiling regardless of env config


def _flash_max_windows() -> int:
    """``CLIPPYME_FLASH_MAX_WINDOWS`` (default 5), clamped to
    ``[1, _FLASH_MAX_WINDOWS_CEILING]`` — how many of a speaker's own
    (longest-first) talk windows get tried when hunting for an avatar frame."""
    try:
        value = int(os.environ.get("CLIPPYME_FLASH_MAX_WINDOWS", "5"))
    except ValueError:
        value = 5
    return max(1, min(_FLASH_MAX_WINDOWS_CEILING, value))


def _pick_speaker_avatars(source_path, video_width, video_height, speaker_windows,
                          seed=0, max_avatars=None):
    """For each speaker (up to ``max_avatars``, in first-appearance order),
    sample a couple of frames INSIDE their own talk windows and take the
    largest confident face found there that doesn't already match an avatar
    already picked for an earlier speaker — instead of guessing from a
    generic frame scan, this ties each avatar to a moment that speaker is
    actually confirmed to be talking. There's no active-speaker-in-frame
    verification here (no MAR/audio gating like reframe does), so two
    diarized speaker IDs can land on the SAME dominant on-screen face (a
    diarization split, or a camera that just keeps one person framed while
    someone else talks) — the signature check below is what catches that,
    not the window selection itself. A speaker with no face found (or only
    duplicate faces) across every sampled window is skipped outright — no
    forced photo (a duplicate that DOES get through is an accepted
    trade-off, not a bug to chase further — see the module-level note).

    ``seed`` rotates which of a speaker's candidate windows is tried FIRST
    (``candidates[seed % n:] + candidates[:seed % n]``) — a "regenerate"
    action re-calls this with a different seed to deliberately land on a
    different frame/face without touching reframe at all. ``seed=0``
    (default) is the original deterministic pick.

    Returns ``(background_frame_path, avatar_sources, scratch_paths)``:
    ``avatar_sources`` is a list of ``(frame_path, box)`` — one entry per
    speaker who got a real match, in speaking order. ``background_frame_path``
    is whichever sampled frame had the most total faces (reused as the blur
    backdrop, no extra extraction pass); it always points at a real file,
    falling back to a frame-0 grab if literally every sample failed.
    ``scratch_paths`` lists every temp PNG created — the caller owns cleanup.
    """
    max_avatars = _flash_max_avatars() if max_avatars is None else max_avatars
    ordered = sorted(speaker_windows.items(), key=lambda kv: min(w[0] for w in kv[1]))

    scratch_paths: list[str] = []
    avatar_sources: list[tuple[str, list]] = []
    avatar_signatures: list[list] = []
    best_bg_path, best_bg_count = None, -1

    for speaker_id, windows in ordered:
        if len(avatar_sources) >= max_avatars:
            break
        # Longest windows first — more chance the speaker is mid-sentence
        # (on camera, mouth moving) rather than caught at a turn boundary.
        candidates = sorted(windows, key=lambda w: w[1] - w[0], reverse=True)[:_flash_max_windows()]
        if seed and candidates:
            shift = seed % len(candidates)
            candidates = candidates[shift:] + candidates[:shift]
        found = None
        for w_start, w_end in candidates:
            mid = (w_start + w_end) / 2.0
            # Several points spread around the window's midpoint (clamped
            # inside the window) instead of one fixed instant — the midpoint
            # alone can land on a blink/turned head even in a long talk
            # window; try nearby moments in the SAME window before giving up
            # on it.
            sample_ts = sorted({
                min(max(mid + offset, w_start), w_end)
                for offset in (0.0, -1.0, 1.0, -2.0, 2.0, -3.0, 3.0)
            })
            for ts in sample_ts:
                fd, frame_path = tempfile.mkstemp(prefix="clippyme-flashspk-", suffix=".png")
                os.close(fd)
                try:
                    _extract_frame_at(source_path, ts, frame_path)
                except Exception:
                    with contextlib.suppress(OSError):
                        os.remove(frame_path)
                    continue
                scratch_paths.append(frame_path)
                faces = _detect_flash_faces(frame_path, video_width, video_height)
                if len(faces) > best_bg_count:
                    best_bg_count, best_bg_path = len(faces), frame_path
                if not faces:
                    continue
                frame_img = Image.open(frame_path).convert("RGB")
                for face in sorted(faces, key=lambda f: f["score"], reverse=True):
                    sig = _face_signature(frame_img, face["box"])
                    if any(_signatures_match(sig, existing) for existing in avatar_signatures):
                        continue  # same face already claimed by an earlier speaker
                    found = (frame_path, face["box"], sig)
                    break
                if found:
                    break
            if found:
                break
        if found:
            frame_path, box, sig = found
            avatar_sources.append((frame_path, box))
            avatar_signatures.append(sig)

    if best_bg_path is None:
        fd, best_bg_path = tempfile.mkstemp(prefix="clippyme-flashspk-bg-", suffix=".png")
        os.close(fd)
        scratch_paths.append(best_bg_path)
        with contextlib.suppress(Exception):
            _extract_frame_at(source_path, 0.0, best_bg_path)

    return best_bg_path, avatar_sources, scratch_paths


def _circular_avatar(source_img, box, diameter, border=6):
    """Crop a padded square around a face ``box`` from ``source_img`` (PIL,
    RGB), resize to ``diameter``, and mask it into a circle with a thin white
    ring — a "cast" avatar chip for the flash card.
    """
    crop = _crop_face_square(source_img, box).resize((diameter, diameter), Image.LANCZOS)

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
    avatar = Image.new("RGBA", (diameter, diameter))
    avatar.paste(crop, (0, 0), mask)
    if border <= 0:
        return avatar

    ringed = Image.new("RGBA", (diameter + border * 2, diameter + border * 2), (0, 0, 0, 0))
    ImageDraw.Draw(ringed).ellipse(
        (0, 0, diameter + border * 2, diameter + border * 2), fill=(255, 255, 255, 255))
    ringed.paste(avatar, (border, border), avatar)
    return ringed


def add_intro_flash(video_path, text, output_path, style=None, font_scale=1.0,
                    duration=0.5, position="top", frame_source_path=None,
                    speaker_windows=None, avatar_seed=0):
    """Prepend a short (default 0.5s) static title-card before the clip.

    A "pattern interrupt" flash, distinct from the on-video hook overlay: a
    background frame, blurred as a full-bleed background, with the hook text
    and (when a face is detected) a row of circular "cast" avatars — held as
    one still frame for ``duration`` seconds, then cut into ``video_path``
    (unmodified). Kept short deliberately: a static card read for multiple
    seconds reads as "the video hasn't started" and hurts short-form
    retention; this is meant as a sub-second flash, not a title screen.

    ``speaker_windows`` (see ``_speaker_time_windows``), when given and
    non-empty, ties each avatar to a specific diarized speaker: sampled from
    a moment INSIDE that speaker's own talk window, not a generic frame scan
    — a speaker with no confirmed face is skipped, never guessed. ``None``/
    empty (no diarization on this transcript) falls back to the plain
    busiest-frame scan across the whole clip — see ``_pick_best_flash_frame``.

    ``frame_source_path`` is where the background/avatar frame(s) are
    extracted from — defaults to ``video_path`` itself. Pass the clip's
    PRE-compose render here when ``video_path`` already has the on-video hook
    (or any other overlay) burned in for the first few seconds: extracting
    frame 0 from an already-hooked clip would bake that hook text right into
    the blurred background and the face crop.

    ``position`` reuses the hook's own top/center/bottom setting to decide
    the text/avatar arrangement: 'top' puts the text above the avatar row,
    'bottom' puts it below (avatars on top), 'center' (no natural split)
    falls back to the 'top' arrangement. No faces detected → plain
    text-only card, unaffected by ``position``.

    ``avatar_seed`` (speaker-diarized path only) rotates which candidate
    window/frame is tried first per speaker — see ``_pick_speaker_avatars``.
    A "regenerate avatars" action re-composes with a bumped seed to
    deliberately land on a different frame without any reframe re-render.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")
    frame_source_path = frame_source_path or video_path
    if not os.path.exists(frame_source_path):
        raise FileNotFoundError(f"Video {frame_source_path} not found")

    try:
        probe = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "csv=s=x:p=0", video_path,
        ], timeout=30).decode().strip().split("\n")[0]
        w_s, h_s, fps_s = probe.split("x")
        video_width, video_height = int(w_s), int(h_s)
        num, den = fps_s.split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except Exception:
        video_width, video_height, fps = 1080, 1920, 30.0

    scratch_paths = []
    fd_hook, hook_path_tmp = tempfile.mkstemp(prefix="clippyme-flashhook-", suffix=".png")
    os.close(fd_hook)
    fd_card, card_path = tempfile.mkstemp(prefix="clippyme-flashcard-", suffix=".png")
    os.close(fd_card)
    fd_vid, flash_video_path = tempfile.mkstemp(prefix="clippyme-flashvid-", suffix=".mp4")
    os.close(fd_vid)

    try:
        if speaker_windows:
            # Diarized: tie each avatar to a moment INSIDE that speaker's own
            # talk window, skipping anyone with no confirmed face — see
            # _pick_speaker_avatars.
            first_frame_path, avatar_sources, sampled = _pick_speaker_avatars(
                frame_source_path, video_width, video_height, speaker_windows,
                seed=avatar_seed)
            scratch_paths.extend(sampled)
        else:
            # No diarization on this transcript — scan several points across
            # the WHOLE clip for the moment with the most of the cast on
            # screen at once; see _pick_best_flash_frame.
            first_frame_path, faces = _pick_best_flash_frame(
                frame_source_path, video_width, video_height)
            scratch_paths.append(first_frame_path)
            avatar_sources = [(first_frame_path, f["box"]) for f in faces]

        card = ImageOps.fit(
            Image.open(first_frame_path).convert("RGB"),
            (video_width, video_height), method=Image.LANCZOS,
        ).filter(ImageFilter.GaussianBlur(24))

        target_box_width = int(video_width * 0.9)
        hook_path, box_w, box_h = create_hook_image(
            text, target_box_width, hook_path_tmp, font_scale=font_scale, style=style,
        )
        hook_img = Image.open(hook_path).convert("RGBA")

        if avatar_sources:
            # Cast avatars: 1-2 people share the same (larger) chip size —
            # two still fit one row comfortably at full size. 3+ shrinks a
            # bit to avoid crowding, but stays noticeably bigger than before.
            diameter = int(video_width * {1: 0.26, 2: 0.26}.get(len(avatar_sources), 0.20))
            gap = int(diameter * 0.16)
            avatars = [
                _circular_avatar(Image.open(p).convert("RGB"), box, diameter)
                for p, box in avatar_sources
            ]
            row_w = sum(a.width for a in avatars) + gap * (len(avatars) - 1)
            v_gap = int(video_height * 0.03)
            block_h = box_h + v_gap + avatars[0].height
            top = (video_height - block_h) // 2
            avatars_first = position == "bottom"  # 'center' falls back to 'top' below
            hook_y = top + avatars[0].height + v_gap if avatars_first else top
            avatar_y = top if avatars_first else top + box_h + v_gap
            card.paste(hook_img, ((video_width - box_w) // 2, hook_y), hook_img)
            x = (video_width - row_w) // 2
            for avatar in avatars:
                card.paste(avatar, (x, avatar_y), avatar)
                x += avatar.width + gap
        else:
            card.paste(hook_img, ((video_width - box_w) // 2, (video_height - box_h) // 2), hook_img)
        card.save(card_path)

        # Build the flash as its own short silent clip first — simpler and more
        # robust than a single combined command, and an explicit -t on the
        # output bounds BOTH the looped image and the (also infinite) anullsrc
        # input directly; see build_hook_overlay_filter's shortest=1 note above
        # for why relying on a muxer-level -shortest is the wrong tool when
        # any input has no natural duration.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-i", card_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(duration),
                "-r", str(fps),
                *x264_video_args(),
                "-c:a", "aac",
                flash_video_path,
            ],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=ffmpeg_timeout(),
        )

        # Concat via filter (re-encodes both segments, so codec/timebase
        # differences between the flash and the main clip never matter) —
        # aformat normalizes both audio tracks to one format first so a
        # source clip with different sample rate/channels still concats clean.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", flash_video_path,
                "-i", video_path,
                "-filter_complex",
                "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
                "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
                "[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[v][a]",
                "-map", "[v]", "-map", "[a]",
                *x264_video_args(),
                "-c:a", "aac",
                output_path,
            ],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=ffmpeg_timeout(),
        )
        logger.info("✅ Intro flash prepended to %s", output_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("❌ FFmpeg intro flash timed out after %ss", ffmpeg_timeout())
        raise
    except subprocess.CalledProcessError as e:
        logger.error("❌ FFmpeg Error: %s", e.stderr.decode() if e.stderr else "Unknown")
        raise
    finally:
        for p in (*scratch_paths, hook_path_tmp, card_path, flash_video_path):
            if not p:
                continue
            with contextlib.suppress(OSError):
                os.remove(p)
