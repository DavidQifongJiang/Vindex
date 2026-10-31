// Run against an opt-in frontend build served on VINDEX_UI_URL (default :5174).
// All API and media responses are fixtures; no real user data is modified.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const video = { video_id: 'video-1', title: 'Algorithms lecture', owner_user_id: 'owner', filename: 'lecture.mp4',
      status: 'processed', processed_path: 'video.mp4', embedding_status: 'completed',
      transcript_status: 'completed', segments_status: 'completed', visibility: 'private' };
    const coverage = { complete: true, total_videos: 1, processing_videos: 0, indexing_failed_videos: 0,
      eligible_videos: { semantic: 1, lexical: 1, either: 1 } };
    const hit = { video_id: video.video_id, title: video.title, revision_id: 'revision', passage_id: 'passage',
      start_seconds: 12, end_seconds: 15, text: 'Dijkstra uses a Fibonacci heap. <script>unsafe</script>', matched_by: ['lexical'] };
    const base = { request_id: 'test', degraded: false, coverage, notices: [], results: [hit] };
    const wav = Buffer.alloc(44 + 8000 * 2 * 30);
    wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
    wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32);
    wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
    await page.route('**/*', async route => {
      const path = new URL(route.request().url()).pathname;
      let data;
      let status = 200;
      if (path === '/me') data = { user_id: 'owner', name: 'Test user' };
      else if (path === '/videos' || path === '/public/videos') data = { videos: [video] };
      else if (path.endsWith('/status')) data = video;
      else if (path.endsWith('/metrics')) data = {};
      else if (path.endsWith('/transcripts')) data = { transcript: 'Fibonacci heap' };
      else if (path.endsWith('/segments')) data = { segments: [] };
      else if (path.endsWith('/thumbnail-url')) return route.fulfill({ status: 404, json: { detail: 'No thumbnail' } });
      else if (path.endsWith('/file-url')) data = { url: new URL('/test.wav', route.request().url()).href };
      else if (path === '/test.wav') {
        const range = route.request().headers().range?.match(/bytes=(\d+)-(\d*)/);
        const start = range ? Number(range[1]) : 0;
        const end = range?.[2] ? Number(range[2]) : wav.length - 1;
        return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav',
          headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${wav.length}` } : {}) },
          body: wav.subarray(start, end + 1) });
      }
      else if (path.endsWith('/video-1/search')) data = { results: [{ start: 3, end: 5, text: 'Within-video match', score: 1 }] };
      else if (path === '/search') {
        const body = route.request().postDataJSON();
        assert.equal(body.mode, 'semantic'); assert.equal(body.scope, 'my_library');
        data = structuredClone(base);
        if (body.query === 'empty') data.results = [];
        if (body.query === 'partial') { data.degraded = true; data.coverage.complete = false; data.notices = ['semantic_unavailable', 'library_indexing_incomplete']; }
        if (body.query === 'indexing') { status = 409; data.results = []; data.notices = ['library_indexing_incomplete', 'video_processing']; }
        if (body.query === 'failed') { status = 409; data.results = []; data.notices = ['indexing_failed']; }
        if (body.query === 'outage') { status = 503; data.results = []; }
        if (body.query === 'busy') { status = 429; data = { detail: { code: 'library_search_overloaded' } }; }
        if (body.query === 'disabled') { status = 404; data = { detail: { code: 'library_search_disabled' } }; }
      } else return route.continue();
      return route.fulfill({ status, json: data });
    });
    await page.goto(process.env.VINDEX_UI_URL || 'http://127.0.0.1:5174');
    if (process.env.VINDEX_EXPECT_SEARCH_DISABLED === 'true') {
      await page.getByRole('heading', { name: 'My videos' }).waitFor();
      assert.equal(await page.locator('.library-search').count(), 0);
      console.log('PASS: library search hidden in default build');
      return;
    }
    const panel = page.locator('.library-search');
    async function search(query, expected) {
      await panel.getByRole('searchbox').fill(query);
      await panel.getByRole('button', { name: 'Search', exact: true }).click();
      await panel.getByText(expected, { exact: false }).first().waitFor();
    }
    await search('partial', 'Search is temporarily limited');
    assert.equal(await panel.locator('script').count(), 0);
    await page.screenshot({ path: 'tmp/phase4-library-desktop.png', fullPage: true });
    await search('empty', 'No matches for');
    await search('indexing', 'No passages are searchable yet');
    await search('failed', 'Search preparation failed');
    await search('outage', 'Search is temporarily unavailable');
    await search('busy', 'Search is busy');
    await search('disabled', 'Library search is not enabled yet');
    await search('heap', '1 match for');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'tmp/phase4-library-mobile.png', fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await panel.getByRole('button', { name: /Algorithms lecture/ }).click();
    try {
      await page.waitForFunction(() => { const v = document.querySelector('video'); return v && Math.abs(v.currentTime - 12) < .1; }, null, { timeout: 10000 });
    } catch (error) {
      console.error(await page.evaluate(() => { const v = document.querySelector('video'); return { text: document.body.innerText, video: v && { time: v.currentTime, duration: v.duration, state: v.readyState, error: v.error?.message, src: v.src } }; }));
      throw error;
    }
    await page.getByPlaceholder('Search inside this video').fill('heap');
    await page.locator('.search-panel').getByRole('button', { name: 'Search', exact: true }).click();
    await page.locator('.moments-row').getByText('Within-video match', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Library', exact: true }).click();
    await page.getByRole('button', { name: 'Public explore', exact: true }).click();
    assert.equal(await page.locator('.library-search').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: search, partial/indexing/failure/empty/error states, safe snippets, mobile layout, timestamp playback, within-video search, public-feed exclusion');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
