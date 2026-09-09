// YYYY-MM-DD for the publish `start_date`, offset by `addDays`, computed in
// `timeZone` (the Zernio account's scheduling timezone) rather than the
// browser's local timezone — the backend's SmartScheduler picks slots and
// resolves "today" in that same timezone, so a browser-local date can be off
// by a day from what the operator sees scheduled.
//
// `now` is injectable for tests; callers omit it.
export function localDatePlus(addDays, now = new Date(), timeZone = "Asia/Jakarta") {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type) => parts.find((p) => p.type === type).value;
  const d = new Date(Date.UTC(+get("year"), +get("month") - 1, +get("day")));
  d.setUTCDate(d.getUTCDate() + addDays);
  return d.toISOString().slice(0, 10);
}

// Add `addDays` days to a YYYY-MM-DD string (date-only, no timezone
// ambiguity — used to offset a user-picked or default start date per clip
// in a batch).
export function addDaysToDateString(dateStr, addDays) {
  const [y, m, day] = dateStr.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1, day));
  d.setUTCDate(d.getUTCDate() + addDays);
  return d.toISOString().slice(0, 10);
}
