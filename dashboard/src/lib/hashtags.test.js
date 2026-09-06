import { test } from 'vitest';
import assert from 'node:assert/strict';
import { combineHashtags, renderCaptionTemplate } from './hashtags.js';

test('combineHashtags dedupes case-insensitively, order preserved', () => {
  assert.deepEqual(combineHashtags(['#Kick', 'gaming', 'kick']), ['#Kick', '#gaming']);
});

test('combineHashtags handles missing/undefined input', () => {
  assert.deepEqual(combineHashtags(undefined), []);
  assert.deepEqual(combineHashtags(null), []);
});

test('renderCaptionTemplate fills placeholders and trims', () => {
  assert.equal(
    renderCaptionTemplate('{caption}\n\n{hashtagai}', { caption: 'Keren', hashtagai: '' }),
    'Keren',
  );
  assert.equal(
    renderCaptionTemplate('{title} — {hashtagai}', { title: 'Judul', hashtagai: '#a #b' }),
    'Judul — #a #b',
  );
});

test('renderCaptionTemplate leaves unknown placeholders blank', () => {
  assert.equal(renderCaptionTemplate('{nope}', {}), '');
});
