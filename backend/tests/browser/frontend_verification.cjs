// Isolated redesign regressions. Every API/media request is fulfilled locally.
// Run with PLAYWRIGHT_MODULE, VINDEX_UI_URL (default :5185), and VINDEX_AUTH_UI_URL (:5186).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { launchOptions } = require('./harness.cjs');

const output = process.env.VINDEX_VERIFY_OUTPUT || '.review-cache/frontend-verification/browser';
const ui = process.env.VINDEX_UI_URL || 'http://127.0.0.1:5185';
const authUi = process.env.VINDEX_AUTH_UI_URL || 'http://127.0.0.1:5186';
fs.mkdirSync(output, { recursive: true });
const video = { video_id: 'video-1', title: 'Algorithms lecture', owner_user_id: 'owner', filename: 'lecture.mp4',
  status: 'processed', processed_path: 'video.mp4', embedding_status: 'completed', audio_status: 'completed',
  transcript_status: 'completed', segments_status: 'completed', visibility: 'private', created_at: '2026-10-30T12:00:00Z' };
const publicVideo = { ...video, video_id: 'public-1', title: 'Public lecture', owner_user_id: 'another-owner', visibility: 'public' };
const wav = Buffer.alloc(44 + 8000 * 2 * 30);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const settle = page => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

async function fixture(browser, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const state = { videos: [{ ...video }], publicVideos: [{ ...publicVideo }], uploads: [], deletes: [], searches: [],
    errors: [], unexpected: [], thumbnails: [], uploadFailures: 0, listFailures: 0, deleteFailures: 0, ...options };
  page.on('pageerror', error => state.errors.push(error.message));
  if (options.session) await context.addInitScript(session => localStorage.setItem('vindex:authSession', JSON.stringify(session)), options.session);
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    let data;
    if (pathname === '/me') data = { user_id: 'owner', name: 'Test user' };
    else if (pathname === '/videos/upload') {
      state.uploads.push({ key: request.headers()['idempotency-key'], body: request.postDataBuffer().toString() });
      if (state.uploadFailures-- > 0) return route.fulfill({ status: 503, json: { detail: 'Upload unavailable; retry.' } });
      const uploaded = { ...video, video_id: 'uploaded-1', title: 'Fixture upload', status: 'queued', processed_path: null,
        transcript_status: 'pending', segments_status: 'pending', embedding_status: 'pending' };
      state.videos.push(uploaded); data = uploaded;
    } else if (pathname === '/videos') {
      if (state.listGate) await state.listGate.promise;
      if (state.listFailures-- > 0) return route.fulfill({ status: 503, json: { detail: 'Library temporarily unavailable' } });
      data = { videos: state.videos };
    } else if (pathname === '/public/videos') data = { videos: state.publicVideos };
    else if (/^\/videos\/[^/]+$/.test(pathname) && request.method() === 'DELETE') {
      state.deletes.push(pathname);
      if (state.deleteFailures-- > 0) return route.fulfill({ status: 503, json: { detail: 'Delete failed; retry.' } });
      state.videos = state.videos.filter(v => pathname !== `/videos/${v.video_id}`);
      data = { status: 'deleted' };
    } else if (pathname.endsWith('/status')) {
      const id = pathname.split('/')[2];
      data = [...state.videos, ...state.publicVideos].find(v => v.video_id === id);
      if (!data) return route.fulfill({ status: 404, json: { detail: 'Video unavailable' } });
    } else if (pathname.endsWith('/metrics')) data = { upload: { uploaded_bytes: 8192 }, search: { result_count: 1 } };
    else if (pathname.endsWith('/transcripts')) data = { transcript: 'Fibonacci heap. <script>unsafe</script> More detail.' };
    else if (pathname.endsWith('/segments')) data = { segments: [{ start: 0, end: 3, text: 'Introduction' }, { start: 3, end: 6, text: 'Fibonacci heap. <script>unsafe</script>' }] };
    else if (pathname.endsWith('/thumbnail-url')) {
      state.thumbnails.push(pathname);
      return route.fulfill({ status: 404, json: { detail: 'No thumbnail' } });
    }
    else if (pathname.endsWith('/file-url')) data = { url: new URL('/fixture.wav', request.url()).href };
    else if (pathname === '/fixture.wav') {
      const range = request.headers().range?.match(/bytes=(\d+)-(\d*)/);
      const start = range ? Number(range[1]) : 0;
      const end = range?.[2] ? Number(range[2]) : wav.length - 1;
      return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${wav.length}` } : {}) }, body: wav.subarray(start, end + 1) });
    } else if (/^\/videos\/[^/]+\/search$/.test(pathname)) {
      const body = request.postDataJSON(); state.searches.push(body);
      if (state.searchGate) await state.searchGate.promise;
      data = { results: body.query === 'empty' ? [] : [{ start: 3, end: 4, score: 1, text: 'Fibonacci heap. <script>unsafe</script>' }] };
    } else if (pathname === '/search') {
      data = { request_id: 'fixture-search', degraded: false, notices: [], results: [],
        coverage: { complete: true, total_videos: 1, processing_videos: 0, indexing_failed_videos: 0, eligible_videos: { semantic: 1, lexical: 1, either: 1 } } };
    } else if (url.origin === new URL(options.auth ? authUi : ui).origin && (pathname === '/' || pathname.startsWith('/assets/'))) return route.continue();
    else { state.unexpected.push(request.method() + ' ' + url.origin + pathname); return route.abort(); }
    return route.fulfill({ status: 200, json: data });
  });
  await page.goto(options.auth ? authUi : ui);
  await page.getByRole('heading', { name: 'My videos', exact: true }).waitFor();
  return { context, page, state };
}

(async () => {
  const browser = await chromium.launch(launchOptions());
  const results = [];
  async function check(name, fn, options) {
    const f = await fixture(browser, options);
    try {
      await fn(f);
      assert.deepEqual(f.state.errors, [], 'Uncaught browser errors');
      assert.deepEqual(f.state.unexpected, [], 'Unexpected external/API traffic');
      results.push({ name, status: 'passed' });
      console.log('PASS:', name);
    } catch (error) {
      results.push({ name, status: 'failed', error: error.message });
      console.error('FAIL:', name, error.message);
      await f.page.screenshot({ path: path.join(output, name + '-failure.png'), fullPage: true }).catch(() => {});
    } finally { await f.context.close(); }
  }
  try {
    await check('upload-retry-processing-playback', async ({ page, state }) => {
      await page.getByRole('button', { name: 'Upload video', exact: true }).first().click();
      const dialog = page.getByRole('dialog');
      assert.equal(await dialog.getByRole('button', { name: 'Upload video', exact: true }).isDisabled(), true);
      await dialog.locator('input[type=file]').setInputFiles({ name: 'fixture.mp4', mimeType: 'video/mp4', buffer: Buffer.from('test-only-upload') });
      await dialog.getByLabel('Title', { exact: true }).fill('Fixture upload');
      assert.equal(await dialog.getByLabel('Visibility').inputValue(), 'private');
      state.uploadFailures = 1;
      await dialog.getByRole('button', { name: 'Upload video', exact: true }).click();
      await dialog.getByRole('alert').filter({ hasText: 'Upload unavailable' }).waitFor();
      await dialog.getByRole('button', { name: 'Upload video', exact: true }).click();
      await page.getByRole('heading', { name: 'Fixture upload', exact: true }).waitFor();
      assert.equal(state.uploads.length, 2);
      assert.ok(state.uploads[0].key);
      assert.equal(state.uploads[0].key, state.uploads[1].key);
      assert.match(state.uploads[0].body, /name="visibility"\r\n\r\nprivate/);
      await page.getByText('Search and the transcript will appear after processing.').waitFor();
      Object.assign(state.videos.find(v => v.video_id === 'uploaded-1'), { ...video, video_id: 'uploaded-1', title: 'Fixture upload' });
      await page.getByRole('button', { name: 'Details', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
      await page.keyboard.press('Escape');
      await page.getByRole('searchbox', { name: 'Search inside this video' }).waitFor();
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1);
    });
    await check('player-moments-keyboard-and-transcript', async ({ page }) => {
      await page.getByRole('button', { name: /Algorithms lecture/ }).first().click();
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1);
      const player = page.getByRole('group', { name: 'Video player', exact: true });
      await player.getByRole('button', { name: 'Play', exact: true }).click();
      await player.getByRole('button', { name: 'Pause', exact: true }).click();
      await player.focus(); await page.keyboard.press('ArrowRight');
      assert.ok(await page.locator('video').evaluate(v => v.currentTime >= 5));
      await player.getByRole('button', { name: 'Mute', exact: true }).click();
      assert.equal(await page.locator('video').evaluate(v => v.muted), true);
      await player.getByRole('button', { name: 'Unmute', exact: true }).click();
      await player.getByRole('slider', { name: 'Volume', exact: true }).fill('0.5');
      assert.equal(await page.locator('video').evaluate(v => v.volume), 0.5);
      await player.getByRole('button', { name: 'Fullscreen', exact: true }).click();
      await page.waitForFunction(() => !!document.fullscreenElement);
      await player.getByRole('button', { name: 'Exit fullscreen', exact: true }).click();
      await page.waitForFunction(() => !document.fullscreenElement);
      const panel = page.locator('.search-panel');
      await panel.getByRole('searchbox').fill('heap'); await panel.getByRole('searchbox').press('Enter');
      await page.locator('.moments-row').getByRole('button', { name: /Fibonacci heap/ }).click();
      await page.getByRole('button', { name: 'Keep watching', exact: true }).waitFor();
      assert.equal(await page.locator('video').evaluate(v => v.paused), true);
      await page.getByRole('button', { name: 'Keep watching', exact: true }).click();
      assert.equal(await page.locator('video').evaluate(v => v.paused), false);
      await player.getByRole('button', { name: 'Pause', exact: true }).click();
      await panel.getByRole('tab', { name: 'Transcript', exact: true }).click();
      await panel.getByRole('tabpanel').getByText('<script>unsafe</script>', { exact: false }).waitFor();
      assert.equal(await panel.locator('script').count(), 0);
      await page.screenshot({ path: path.join(output, 'watch-desktop.png'), fullPage: true });
      for (const width of [320, 390, 768]) {
        await page.setViewportSize({ width, height: 844 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Watch overflow at ${width}`);
      }
      await page.screenshot({ path: path.join(output, 'watch-tablet.png'), fullPage: true });
    });
    await check('delete-cancel-failure-retry-and-ownership', async ({ page, state }) => {
      await page.getByRole('button', { name: 'Delete Algorithms lecture', exact: true }).click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel', exact: true }).click();
      await page.getByRole('alertdialog').waitFor({ state: 'hidden' });
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'Delete Algorithms lecture', null, { timeout: 1500 });
      assert.equal(state.deletes.length, 0);
      state.deleteFailures = 1;
      await page.getByRole('button', { name: 'Delete Algorithms lecture', exact: true }).click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Delete video', exact: true }).click();
      await page.getByRole('status').filter({ hasText: 'Delete failed' }).waitFor();
      await page.getByRole('button', { name: 'Delete Algorithms lecture', exact: true }).click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Delete video', exact: true }).click();
      await page.getByRole('heading', { name: 'No videos yet', exact: true }).waitFor();
      await page.getByRole('button', { name: 'Public explore', exact: true }).click();
      await page.getByRole('heading', { name: 'Public lecture', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Delete Public lecture', exact: true }).count(), 0);
    });
    await check('theme-responsive-layout-and-dialog-focus', async ({ page }) => {
      await page.getByRole('heading', { name: 'Algorithms lecture', exact: true }).waitFor();
      for (const width of [320, 390, 768, 1280]) {
        await page.setViewportSize({ width, height: 844 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Library overflow at ${width}`);
        await page.screenshot({ path: path.join(output, `library-${width}.png`), fullPage: true });
      }
      await page.getByRole('switch', { name: 'Dark theme', exact: true }).check();
      await page.reload();
      assert.equal(await page.locator('html').evaluate(el => el.classList.contains('dark')), true);
      await page.screenshot({ path: path.join(output, 'library-dark.png'), fullPage: true });
      const upload = page.getByRole('button', { name: 'Upload video', exact: true }).first();
      await upload.click();
      for (let i = 0; i < 9; i++) {
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => !!document.activeElement.closest('[role=dialog]')), true);
      }
      await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.waitForFunction(() => document.activeElement?.tagName === 'BUTTON' && document.activeElement.textContent.trim() === 'Upload video', null, { timeout: 1500 });
      assert.equal(await upload.evaluate(el => el === document.activeElement), true, 'Upload dialog must return keyboard focus');
    });
    await check('list-error-recovery-and-processing-failure', async ({ page, state }) => {
      await page.getByRole('status').filter({ hasText: 'Library temporarily unavailable' }).waitFor();
      state.videos = [{ ...video, status: 'failed', processed_path: null, embedding_status: 'failed', error_message: 'Fixture processing failure' }];
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await page.getByRole('button', { name: /Algorithms lecture/ }).first().click();
      await page.getByText('Fixture processing failure', { exact: true }).waitFor();
      await page.getByText("Search and the transcript aren't available because processing failed.").waitFor();
    }, { listFailures: 1 });
    await check('details-restores-focus', async ({ page }) => {
      await page.getByRole('button', { name: /Algorithms lecture/ }).first().click();
      const details = page.getByRole('button', { name: 'Details', exact: true });
      await details.click();
      await page.getByRole('dialog').getByRole('heading', { name: 'Details', exact: true }).waitFor();
      await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.waitForFunction(() => document.activeElement?.tagName === 'BUTTON' && document.activeElement.textContent.trim() === 'Details', null, { timeout: 1500 });
      assert.equal(await details.evaluate(el => el === document.activeElement), true);
    });
    await check('thumbnail-follows-playback-readiness', async ({ page, state }) => {
      await page.getByRole('heading', { name: 'Algorithms lecture', exact: true }).waitFor();
      assert.equal(state.thumbnails.length, 0);
      state.videos[0].processed_path = 'ready-playback.mp4';
      // Background polling keeps cards mounted, so this exercises the effect dependencies.
      await page.getByRole('button', { name: /Algorithms lecture/ }).first().click();
      await page.waitForFunction(() => !!document.querySelector('video'));
      await page.waitForResponse(r => new URL(r.url()).pathname === '/videos', { timeout: 6000 });
      await settle(page);
      assert.equal(state.thumbnails.length, 1, 'A newly available playback artifact should trigger the thumbnail request');
    }, { videos: [{ ...video, status: 'processing', processed_path: null, embedding_status: 'pending' }] });
    await check('auth-guest-and-public-access', async ({ page }) => {
      await page.getByRole('heading', { name: 'Sign in to see your videos', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Upload video', exact: true }).first().isDisabled(), true);
      await page.getByRole('button', { name: 'Sign in', exact: true }).waitFor();
      await page.getByRole('button', { name: 'Public explore', exact: true }).click();
      await page.getByRole('heading', { name: 'Public lecture', exact: true }).waitFor();
    }, { auth: true });
    await check('auth-stored-session', async ({ page }) => {
      await page.getByRole('heading', { name: 'Algorithms lecture', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Upload video', exact: true }).first().isEnabled(), true);
      await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor();
    }, { auth: true, session: { idToken: 'fixture-id', accessToken: 'fixture-access', expiresAt: Date.now() + 3600000, profile: { user_id: 'owner' } } });
    await check('auth-expired-session', async ({ page }) => {
      await page.getByRole('heading', { name: 'Sign in to see your videos', exact: true }).waitFor();
      assert.equal(await page.evaluate(() => localStorage.getItem('vindex:authSession')), null);
    }, { auth: true, session: { idToken: 'expired-fixture', expiresAt: 1 } });
    await check('navigation-rejects-stale-library-response', async ({ page, state }) => {
      await page.getByRole('heading', { name: 'Algorithms lecture', exact: true }).waitFor();
      state.listGate = deferred();
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      const publicResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/public/videos');
      await page.getByRole('button', { name: 'Public explore', exact: true }).click();
      await publicResponse;
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/videos');
      state.listGate.resolve(); await response; await settle(page);
      assert.equal(await page.getByRole('heading', { name: 'Algorithms lecture', exact: true }).count(), 0, 'Private library response must not replace public feed');
    });
    await check('clear-cancels-in-flight-video-search', async ({ page, state }) => {
      await page.getByRole('button', { name: /Algorithms lecture/ }).first().click();
      const panel = page.locator('.search-panel');
      await panel.getByRole('searchbox').fill('heap'); await panel.getByRole('searchbox').press('Enter');
      await panel.getByText('1 highlighted below').waitFor();
      state.searchGate = deferred();
      await panel.getByRole('searchbox').fill('new query'); await panel.getByRole('searchbox').press('Enter');
      await page.waitForFunction(() => document.querySelector('.search-panel button[aria-label=Search]').disabled);
      await panel.getByRole('button', { name: 'Clear', exact: true }).click();
      const response = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/video-1/search'));
      state.searchGate.resolve(); await response; await settle(page);
      assert.equal(await page.locator('.moments-row').count(), 0, 'Cleared results must not reappear after a delayed response');
    });
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  }
  if (results.some(r => r.status === 'failed')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
