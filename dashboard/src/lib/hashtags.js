// Pure helpers for combining AI-generated (Gemini, per-clip) + static
// hashtags. Mirrors clippyme.domain.live_monitor's Python equivalents
// (parse_static_hashtags / combine_hashtags) so behavior matches between the
// Live Monitor auto-publish path and the manual create/history publish flow.
const SPLIT_RE = /[\s,]+/;

function normalizeHashtag(tag) {
  return String(tag || "").replace(/[^a-zA-Z0-9]/g, "");
}

// Free-text "static hashtags" field -> ['#a', '#b', ...]. Accepts space- or
// comma-separated input, with or without a leading '#'.
export function parseStaticHashtags(raw) {
  if (!raw) return [];
  return raw
    .split(SPLIT_RE)
    .map(normalizeHashtag)
    .filter(Boolean)
    .map((t) => `#${t}`);
}

// Merge AI + static hashtags, case-insensitive deduped (AI first, a static
// tag already covered by an AI one is dropped rather than posted twice).
export function combineHashtags(aiTags, staticTags) {
  const seen = new Set();
  const out = [];
  for (const tag of [...(aiTags || []), ...(staticTags || [])]) {
    const norm = normalizeHashtag(tag);
    if (!norm || seen.has(norm.toLowerCase())) continue;
    seen.add(norm.toLowerCase());
    out.push(`#${norm}`);
  }
  return out;
}
