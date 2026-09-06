// Pure helpers mirroring clippyme.domain.live_monitor's Python equivalents
// so behavior matches between the Live Monitor auto-publish path and the
// manual create/history publish flow.

function normalizeHashtag(tag) {
  return String(tag || "").replace(/[^a-zA-Z0-9]/g, "");
}

// Dedupe Gemini's per-clip hashtags case-insensitively, order preserved.
export function combineHashtags(tags) {
  const seen = new Set();
  const out = [];
  for (const tag of tags || []) {
    const norm = normalizeHashtag(tag);
    if (!norm || seen.has(norm.toLowerCase())) continue;
    seen.add(norm.toLowerCase());
    out.push(`#${norm}`);
  }
  return out;
}

// Fill `{name}` placeholders in a user-authored caption template. Unknown
// placeholders resolve to "" rather than erroring, so a typo just drops that
// spot instead of breaking the whole caption. Trimmed at the end so an empty
// placeholder (e.g. no AI hashtags) doesn't leave a dangling blank line.
export function renderCaptionTemplate(template, vars) {
  return String(template || "")
    .replace(/\{\s*(\w+)\s*\}/g, (_, key) => vars?.[key] ?? "")
    .trim();
}

// Drop repeat "#tag" occurrences from an already-rendered caption, case-
// insensitive, first occurrence wins — covers a hashtag the user typed
// literally in the template colliding with one that came from {hashtagai}.
// Leftover double-spaces/blank lines from a removed tag are collapsed.
export function dedupeHashtagsInText(text) {
  const seen = new Set();
  return String(text || "")
    .replace(/#(\w+)/g, (match, word) => {
      const key = word.toLowerCase();
      if (seen.has(key)) return "";
      seen.add(key);
      return match;
    })
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
