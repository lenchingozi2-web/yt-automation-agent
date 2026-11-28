import { test } from 'node:test';
import assert from 'node:assert/strict';
import config from '../cloudflare.config.ts';

test('Cloudflare previews omit production triggers and production retains daily research recovery', () => {
  const preview = config({ isPreview: true, mode: 'production' });
  const production = config({ isPreview: false, mode: 'production' });
  assert.equal(preview.worker.triggers, undefined);
  assert.equal(production.worker.triggers?.length, 2);
  assert.equal(preview.worker.entrypoint, production.worker.entrypoint);
  assert.deepEqual(preview.worker.assets, production.worker.assets);
});
