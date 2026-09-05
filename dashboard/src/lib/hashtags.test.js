import { test } from 'vitest';
import assert from 'node:assert/strict';
import { parseStaticHashtags, combineHashtags } from './hashtags.js';

test('parseStaticHashtags splits on space/comma and strips leading #', () => {
  assert.deepEqual(parseStaticHashtags('#fyp, viral  shorts'), ['#fyp', '#viral', '#shorts']);
});

test('parseStaticHashtags empty input', () => {
  assert.deepEqual(parseStaticHashtags(''), []);
  assert.deepEqual(parseStaticHashtags(undefined), []);
});

test('combineHashtags dedupes case-insensitively, AI first', () => {
  assert.deepEqual(
    combineHashtags(['#Kick', 'gaming'], parseStaticHashtags('kick, #Viral fyp')),
    ['#Kick', '#gaming', '#Viral', '#fyp'],
  );
});

test('combineHashtags handles missing/undefined lists', () => {
  assert.deepEqual(combineHashtags(undefined, undefined), []);
  assert.deepEqual(combineHashtags(null, ['#a']), ['#a']);
});
