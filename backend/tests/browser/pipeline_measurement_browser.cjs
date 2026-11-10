// Real Chromium decoding with fully routed local fixtures; no application services.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { measurePipeline } = require('../../benchmarks/pipeline_measurement.cjs');
const { createBrowserProbes } = require('../../benchmarks/pipeline_browser_probes.cjs');
const { launchOptions, recordPass, captureFailure } = require('./harness.cjs');

(async () => {
  const browser = await chromium.launch(launchOptions());
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const origin = 'https://pipeline.fixture.invalid';
    const token = 'private-fixture-token', signed = 'private-fixture-signature';
    const media = fs.readFileSync(path.join(__dirname, 'fixtures/readiness.webm'));
    const audio = Buffer.alloc(44 + 8000 * 2);
    audio.write('RIFF'); audio.writeUInt32LE(audio.length - 8, 4); audio.write('WAVEfmt ', 8);
    audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22);
    audio.writeUInt32LE(8000, 24); audio.writeUInt32LE(16000, 28); audio.writeUInt16LE(2, 32);
    audio.writeUInt16LE(16, 34); audio.write('data', 36); audio.writeUInt32LE(audio.length - 44, 40);
    let searchCalls = 0, fileCalls = 0, mediaRequests = 0, mode = 'api', wrongIdentity = false, audioOnly = false;
    const unexpected = [];
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin === 'https://media.fixture.invalid') {
        mediaRequests++;
        assert.equal(request.headers().authorization, undefined);
        return route.fulfill({ contentType: audioOnly ? 'audio/wav' : 'video/webm', headers: { 'Cache-Control': 'no-store' },
          body: audioOnly ? audio : fileCalls === 1 ? Buffer.from('broken video') : media });
      }
      if (url.origin === 'https://upload.fixture.invalid') {
        assert.equal(request.headers().authorization, undefined);
        return route.fulfill({ status: 204, body: '' });
      }
      if (url.origin === origin && url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html><body><button>Upload</button></body></html>' });
      assert.equal(request.headers().authorization, `Bearer ${token}`);
      let json;
      if (url.pathname === '/videos/uploads') json = { upload: { url: 'https://upload.fixture.invalid/' } };
      else if (url.pathname === '/videos/upload' || url.pathname === '/videos/uploads/video-1/complete') json = { video_id: 'video-1' };
      else if (url.pathname === '/videos/video-1/search') {
        searchCalls++;
        assert.equal(request.postDataJSON().algorithm, 'qdrant_embedding');
        json = { video_id: wrongIdentity ? 'other-video' : 'video-1', results: searchCalls === 1 ? [] :
          [{ text: searchCalls === 3 ? 'An unrelated vector searchlight.' : 'We use vector search to find moments.',
            start: searchCalls === 2 ? 90 : 1, end: searchCalls === 2 ? 92 : 3 }] };
      } else if (url.pathname === '/videos/video-1/file-url') {
        fileCalls++;
        json = { video_id: 'video-1', url: `https://media.fixture.invalid/video.webm?signature=${signed}&attempt=${fileCalls}` };
      } else { unexpected.push(url.pathname); return route.abort(); }
      return route.fulfill({ json });
    });
    await page.goto(origin);
    // Tiny generated VP8 clip, not user media. HTTP 200 is insufficient without decoding.
    await page.evaluate(value => localStorage.setItem('vindex:authSession', JSON.stringify({ idToken: value })), token);
    const probes = createBrowserProbes(page);
    const config = { deadline_seconds: 8, poll_interval_ms: 100, probe_timeout_ms: 2000,
      search: { query: 'vector search', expected_text: 'vector search', start_seconds: 1, end_seconds: 3, tolerance_seconds: 0.5 } };
    for (mode of ['api', 'direct']) {
      searchCalls = 0; fileCalls = 0;
      await page.evaluate(uploadMode => {
        document.querySelector('button').onclick = async () => {
          const headers = { Authorization: `Bearer ${JSON.parse(localStorage.getItem('vindex:authSession')).idToken}` };
          if (uploadMode === 'api') await fetch('/videos/upload', { method: 'POST', headers });
          else {
            const intent = await (await fetch('/videos/uploads', { method: 'POST', headers })).json();
            await fetch(intent.upload.url, { method: 'POST', mode: 'no-cors' });
            await fetch('/videos/uploads/video-1/complete', { method: 'POST', headers });
          }
        };
      }, mode);
      const events = [];
      const row = await measurePipeline(page, { config, probes, uploadTimeoutMs: 2000,
        start: () => page.getByRole('button', { name: 'Upload', exact: true }).click(), onEvent: event => events.push(event) });
      assert.equal(row.outcome, 'accepted');
      assert.equal(row.observed_mode, mode);
      assert.equal(row.readiness_outcome, 'ready', JSON.stringify({ row, mediaRequests, mediaBytes: media.length }));
      assert.ok(searchCalls >= 4, 'empty results, wrong timestamp and unrelated text must not count as searchable');
      assert.ok(fileCalls >= 2, 'HTTP 200 with broken media must not count as playable');
      assert.ok(row.searchable.observed_seconds >= row.upload_seconds);
      assert.ok(row.playable.observed_seconds >= row.upload_seconds);
      assert.equal(await page.locator('video[data-vindex-pipeline-probe]').count(), 0);
      const evidence = JSON.stringify({ row, events });
      for (const privateValue of [token, signed, 'https://media.fixture.invalid', 'We use vector']) assert.ok(!evidence.includes(privateValue));
    }
    wrongIdentity = true;
    assert.deepEqual(await probes.searchable({ videoId: 'video-1', timeoutMs: 1000, search: config.search }),
      { ready: false, terminal: true, code: 'identity_mismatch' });
    audioOnly = true;
    assert.equal((await probes.playable({ videoId: 'video-1', timeoutMs: 500 })).ready, false, 'Audio-only media is not playable video');
    assert.equal(await page.locator('video[data-vindex-pipeline-probe]').count(), 0);
    assert.deepEqual(unexpected, []);
    await context.close();
    recordPass('pipeline-readiness');
    console.log('Pipeline measurement browser checks passed');
  } catch (error) { await captureFailure(browser); throw error; }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; }); // Fully isolated fixtures only.
