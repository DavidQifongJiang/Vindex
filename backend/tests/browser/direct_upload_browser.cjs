// Real browser + production hash worker; API and S3 responses are isolated fixtures.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { launchOptions, recordPass, captureFailure } = require('./harness.cjs');
const { measureUpload } = require('../../benchmarks/upload_measurement.cjs');
const ui = process.env.VINDEX_UI_URL || 'http://127.0.0.1:5187';
const content = Buffer.alloc(2 * 1024 * 1024 + 17, 0x5a); // Crosses worker chunk boundaries.
const digest = createHash('sha256').update(content).digest('hex');
const encoded = Buffer.from(digest, 'hex').toString('base64');
const video = { video_id: 'upload-1', title: 'Checksum fixture', filename: 'fixture.mp4',
  status: 'uploaded', playable: false, is_owner: true, visibility: 'private',
  embedding_status: 'pending', audio_status: 'pending', transcript_status: 'pending', segments_status: 'pending' };

(async () => {
  const browser = await chromium.launch(launchOptions());
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [], unexpected = [], intents = [], storagePosts = [];
    let completeCalls = 0, stored = false, accepted = false;
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      const pathname = url.pathname;
      let data;
      if (url.origin === 'https://upload.fixture.invalid') {
        assert.equal(request.method(), 'POST');
        assert.equal(request.headers().authorization, undefined, 'Never forward API credentials to S3');
        const body = request.postDataBuffer();
        storagePosts.push(body);
        assert.ok(body.includes(Buffer.from(encoded)), 'Signed digest field must reach S3');
        assert.ok(body.includes(content), 'Original bytes must reach S3');
        // First S3 request fails: retry must preserve the key and checksum.
        if (storagePosts.length === 1) return route.fulfill({ status: 503, body: '' });
        stored = true;
        return route.fulfill({ status: 204, body: '' });
      }
      if (pathname === '/me') data = { user_id: 'owner', name: 'Test user' };
      else if (pathname === '/videos') data = { videos: accepted ? [video] : [] };
      else if (pathname === '/public/videos') data = { videos: [] };
      else if (pathname === '/videos/uploads') {
        const body = request.postDataJSON();
        assert.equal(body.sha256, digest);
        assert.equal(body.size_bytes, content.length);
        assert.ok(request.postDataBuffer().length < 1024, 'API receives metadata only');
        intents.push({ key: request.headers()['idempotency-key'], body });
        data = { video_id: video.video_id, upload: { method: 'POST', url: 'https://upload.fixture.invalid/',
          fields: { key: 'staging/file.mp4', 'Content-Type': 'video/mp4',
            'x-amz-checksum-algorithm': 'SHA256', 'x-amz-checksum-sha256': encoded } } };
      } else if (pathname === '/videos/uploads/upload-1/complete') {
        assert.ok(stored, 'Never complete before a successful S3 response');
        assert.equal(request.headers()['idempotency-key'], intents[0].key);
        completeCalls++;
        accepted = true; data = video;
      } else if (pathname === '/videos/upload-1/status') data = video;
      else if (pathname === '/videos/upload-1/metrics') data = {};
      else if (url.origin === new URL(ui).origin && (pathname === '/' || pathname.startsWith('/assets/'))) return route.continue();
      else { unexpected.push(request.method() + ' ' + url.origin + pathname); return route.abort(); }
      return route.fulfill({ status: 200, json: data });
    });
    await page.goto(ui);
    await page.getByRole('button', { name: 'Upload video', exact: true }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('input[type=file]').setInputFiles({ name: 'fixture.mp4', mimeType: 'video/mp4', buffer: content });
    await dialog.getByLabel('Title', { exact: true }).fill(video.title);
    const failed = await measureUpload(page, { timeoutMs: 10000,
      start: () => dialog.getByRole('button', { name: 'Upload video', exact: true }).click() });
    assert.equal(failed.outcome, 'failed');
    assert.equal(failed.failure_stage, 'storage');
    await dialog.getByRole('alert').filter({ hasText: 'could not be uploaded' }).waitFor();
    assert.equal(completeCalls, 0);
    const measured = await measureUpload(page, { timeoutMs: 10000,
      start: () => dialog.getByRole('button', { name: 'Upload video', exact: true }).click() });
    assert.equal(measured.outcome, 'accepted');
    assert.equal(measured.observed_mode, 'direct');
    assert.ok(measured.upload_seconds > 0);
    assert.ok(measured.pre_request_seconds >= 0);
    assert.ok(measured.storage_seconds >= 0);
    await page.getByRole('heading', { name: video.title, exact: true }).waitFor();
    assert.equal(intents.length, 2);
    assert.ok(intents[0].key);
    assert.equal(intents[0].key, intents[1].key);
    assert.deepEqual(intents[0].body, intents[1].body);
    assert.equal(completeCalls, 1);
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    recordPass('direct-upload-worker-checksum-storage-failure-retry');
    console.log('PASS: direct upload checksum and storage retry');
  } catch (error) {
    await captureFailure(browser);
    console.error(error);
    process.exitCode = 1;
  } finally { await browser.close(); }
})();
