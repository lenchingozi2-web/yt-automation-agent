import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeVideo, normalizeDraft } from '../src/lib/studio.ts';

test('actual production schema retains portrait, timing, tags and publish state through API/client normalization', () => {
  const row = { id: 'video', title: 'Test', status: 'completed', scenes: [
    { narration: 'Every scene has a voice.', search_queries: ['doctor'], _studio: {
      aspect_ratio: '9:16', duration_seconds: 90, tags: ['AI'], thumbnail_title: 'A useful hook',
      video_url: 'https://x.supabase.co/storage/v1/object/public/renders/video.mp4',
      actual_duration_seconds: 88, publish_status: 'auth_required', publish_error: 'Reconnect YouTube'
    } }
  ] };
  const api = normalizeVideo(row);
  const ui = normalizeVideo(api);
  assert.equal(ui.format, '9:16');
  assert.equal(ui.duration_seconds, 90);
  assert.equal(ui.actual_duration_seconds, 88);
  assert.equal(ui.publish_status, 'auth_required');
  assert.equal(ui.publish_error, 'Reconnect YouTube');
  assert.deepEqual(ui.tags, ['AI']);
  assert.equal(ui.thumbnail_title, 'A useful hook');
  assert.ok(ui.video_url?.endsWith('/video.mp4'));
});

test('legacy voiceover alias survives normalization', () => {
  assert.equal(normalizeDraft({ scenes: [{ voiceover: 'Legacy narration.' }] }).scenes[0].narration, 'Legacy narration.');
});
