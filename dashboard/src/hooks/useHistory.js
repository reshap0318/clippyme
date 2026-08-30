
import { useCallback, useEffect, useState } from 'react';
import { HISTORY_KEY, HISTORY_MAX_ITEMS } from '../lib/constants';
import { readStoredJson, removeStoredValue, subscribeStoredJson, writeStoredJson } from '../lib/storage';

const validHistory = (value) => Array.isArray(value);
const normalize = (value) => (Array.isArray(value) ? value.filter((entry) => entry && typeof entry.jobId === 'string') : []);

export function useHistory() {
  const [history, setHistoryState] = useState(() => normalize(readStoredJson(HISTORY_KEY, [], { validate: validHistory })));

  useEffect(() => subscribeStoredJson(HISTORY_KEY, (value) => setHistoryState(normalize(value)), { validate: validHistory }), []);

  const replaceHistory = useCallback((next) => {
    const value = normalize(typeof next === 'function' ? next(history) : next).slice(0, HISTORY_MAX_ITEMS);
    setHistoryState(value);
    writeStoredJson(HISTORY_KEY, value);
  }, [history]);

  const saveToHistory = useCallback((entry) => {
    if (!entry?.jobId) return;
    setHistoryState((previous) => {
      const updated = [entry, ...previous.filter((item) => item.jobId !== entry.jobId)].slice(0, HISTORY_MAX_ITEMS);
      writeStoredJson(HISTORY_KEY, updated);
      return updated;
    });
  }, []);

  // Backfill entries the backend knows about but this browser's localStorage
  // doesn't (job completed in a session that never called saveToHistory).
  // Existing local entries win on conflict — the backend's view is a fallback,
  // not a source of truth for status/metadata.
  const mergeHistory = useCallback((backendEntries) => {
    if (!backendEntries?.length) return;
    setHistoryState((previous) => {
      const known = new Set(previous.map((entry) => entry.jobId));
      const missing = backendEntries.filter((entry) => !known.has(entry.jobId));
      if (!missing.length) return previous;
      const updated = [...previous, ...missing].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0)).slice(0, HISTORY_MAX_ITEMS);
      writeStoredJson(HISTORY_KEY, updated);
      return updated;
    });
  }, []);

  const purgeJobStorage = useCallback((jobId) => {
    removeStoredValue(`clippyme_clip_states_${jobId}`);
    removeStoredValue(`clippyme_preselections_job_${jobId}`);
  }, []);

  const deleteFromHistory = useCallback((jobId) => {
    setHistoryState((previous) => {
      const updated = previous.filter((entry) => entry.jobId !== jobId);
      writeStoredJson(HISTORY_KEY, updated);
      return updated;
    });
    purgeJobStorage(jobId);
  }, [purgeJobStorage]);

  const clearHistory = useCallback(() => {
    setHistoryState((previous) => {
      previous.forEach((entry) => entry?.jobId && purgeJobStorage(entry.jobId));
      return [];
    });
    removeStoredValue(HISTORY_KEY);
  }, [purgeJobStorage]);

  return { history, setHistory: replaceHistory, saveToHistory, mergeHistory, deleteFromHistory, clearHistory };
}
