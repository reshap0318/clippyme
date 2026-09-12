#!/bin/sh
# ClippyMe container entrypoint.
#
# The image runs as root ONLY long enough to normalize ownership of the
# writable, bind-mountable dirs, then drops to the unprivileged appuser.
#
# A host bind mount (./data:/app/data) or a prior `-u root` invocation can leave
# data/ owned by a UID the non-root appuser (999) cannot traverse — that is the
# cause of the `Permission denied: data/config.json` and `data/cache` failures.
# Re-chowning on every boot makes the container self-healing regardless of how
# the host directory got locked.
#
# The privilege drop happens ONLY for the default `uvicorn` server command, so
# `docker compose run --rm -u root backend sh -lc "..."` (the documented
# integration-test path) still gets a real root shell.
set -e

if [ "$(id -u)" = "0" ] && [ "${1:-}" = "uvicorn" ]; then
    for d in /app/data /app/output /app/uploads /app/.cache /app/.config; do
        mkdir -p "$d"
    done
    # Recurse into every writable dir, output/uploads (raw video) included.
    # A top-level-only chown was the original design here (output/uploads can
    # hold many GB, so recursing looked like unwanted boot-time cost) — but
    # swapping the base image (e.g. Ubuntu 22.04 -> 24.04) shifts appuser's
    # auto-assigned UID/GID, orphaning every file a PREVIOUS image's appuser
    # created anywhere under these mounts. That silently broke two things at
    # once: `data/config.json` (500s from the API) AND every pre-existing
    # job's `output/<job>/*_metadata.json` (scan_history()'s per-entry
    # `except: continue` swallowed the PermissionError with zero log line, so
    # old jobs just vanished from the History tab — no error, no clue).
    # Measured cost of the fix: ~1.2s for 28 job dirs / tens of GB of video,
    # so the "large dirs" worry was overblown — correctness wins here.
    chown -R appuser:appuser /app/data /app/.cache /app/.config /app/output /app/uploads 2>/dev/null || true
    exec gosu appuser "$@"
fi

exec "$@"
