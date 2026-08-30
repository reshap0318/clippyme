
import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, test } from 'vitest';
import { HISTORY_KEY } from '../lib/constants';
import { useHistory } from './useHistory';

beforeEach(() => localStorage.clear());

test('mergeHistory backfills backend-only entries without touching existing ones', () => {
  localStorage.setItem(HISTORY_KEY, JSON.stringify([{ jobId: 'local', status: 'complete', timestamp: 2 }]));
  const { result } = renderHook(() => useHistory());
  act(() => result.current.mergeHistory([
    { jobId: 'local', status: 'stale-backend-status', timestamp: 999 },
    { jobId: 'backend-only', status: 'complete', timestamp: 1 },
  ]));
  const ids = result.current.history.map((h) => h.jobId).sort();
  expect(ids).toEqual(['backend-only', 'local']);
  expect(result.current.history.find((h) => h.jobId === 'local').status).toBe('complete');
});

test('mergeHistory is a no-op when every jobId is already known', () => {
  localStorage.setItem(HISTORY_KEY, JSON.stringify([{ jobId: 'a', status: 'complete', timestamp: 1 }]));
  const { result } = renderHook(() => useHistory());
  act(() => result.current.mergeHistory([{ jobId: 'a', status: 'complete', timestamp: 1 }]));
  expect(result.current.history).toHaveLength(1);
});
