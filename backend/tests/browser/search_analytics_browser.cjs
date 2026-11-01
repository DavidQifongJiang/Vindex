// Fixture-driven browser checks; no private user data or live writes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { launchOptions, recordPass, captureFailure } = require('./harness.cjs');
(async () => {
  const browser = await chromium.launch(launchOptions());
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    const interactions = [], searches = [], errors = [];
    let failFeedback = true;
    let delayWithdrawal = false, releaseWithdrawal;
    let withdrawalStartedResolve;
    const withdrawalStarted = new Promise(resolve => { withdrawalStartedResolve = resolve; });
    page.on('pageerror', e => errors.push(e.message));
    const video = { video_id: 'video-1', title: 'Algorithms lecture', status: 'processed', visibility: 'private', is_owner: true, keyword_search_enabled: true };
    const hit = { video_id: 'video-1', revision_id: 'r', passage_id: 'p', title: 'Algorithms lecture', start_seconds: 12, end_seconds: 40, text: 'A heap supports a priority queue.', matched_by: ['semantic'] };
    await page.route('**/*', async route => {
      const path = new URL(route.request().url()).pathname;
      let data;
      if (path === '/me') data = { user_id: 'owner', name: 'Test user' };
      else if (path === '/videos' || path === '/public/videos') data = { videos: [video] };
      else if (path === '/search') {
        const body = route.request().postDataJSON(); searches.push(body);
        data = { request_id: `11111111-1111-4111-8111-${String(searches.length).padStart(12, '0')}`, interaction_enabled: body.analytics_opt_in,
          effective_mode: body.mode, degraded: false, notices: [], results: [hit],
          coverage: { complete: true, total_videos: 1, processing_videos: 0, indexing_failed_videos: 0, eligible_videos: { semantic: 1, lexical: 1, either: 1 } } };
      } else if (path.endsWith('/interactions')) {
        const body = route.request().postDataJSON(); interactions.push(body);
        if (body.event_type === 'search.feedback_submitted' && failFeedback) {
          failFeedback = false; return route.fulfill({ status: 503, json: { detail: 'feedback_not_saved' } });
        }
        return route.fulfill({ status: 202, json: { accepted: true } });
      } else if (path.endsWith('/sharing') || path === '/search-sharing') {
        if (delayWithdrawal) {
          await new Promise(resolve => { releaseWithdrawal = resolve; withdrawalStartedResolve(); });
        }
        return route.fulfill({ status: 204 });
      }
      else if (path === '/search-analytics') data = { retention_days: 7, pending_events: 0, pending_bytes: 0, expired_events: 0,
        quarantined: 0, latest_update: '2026-09-18T03:00:00Z',
        modes: { 'synthetic:semantic:fixture-v1': { searches: 1000, p50_ms: 92, p95_ms: 149, errors: 0, zero_results: 12, degraded: 0, viewed: 900, opened: 600, helpful: 80, not_helpful: 20 } },
        review: [{ search_id: 'example', query: 'Why does a heap help?', mode: 'semantic', reason: 'negative_feedback' }] };
      else if (path.endsWith('/status')) data = video;
      else if (path.endsWith('/metrics')) data = {};
      else if (path.endsWith('/transcripts')) data = { transcript: 'Heap' };
      else if (path.endsWith('/segments')) data = { segments: [] };
      else if (path.endsWith('/thumbnail-url') || path.endsWith('/file-url')) return route.fulfill({ status: 404, json: { detail: 'fixture unavailable' } });
      else return route.continue();
      return route.fulfill({ status: 200, json: data });
    });
    await page.goto(process.env.VINDEX_UI_URL || 'http://127.0.0.1:5175');
    const panel = page.locator('.library-search');
    await panel.getByRole('searchbox').fill('Why does a heap help?');
    await panel.getByRole('button', { name: 'Search', exact: true }).click();
    await panel.getByText('1 match for').waitFor();
    assert.equal(searches[0].mode, 'semantic');
    assert.equal(searches[0].analytics_opt_in, false);
    assert.equal(interactions.length, 0);
    await panel.getByLabel('Search mode').selectOption('lexical');
    await panel.getByLabel('Help measure search performance', { exact: false }).check();
    await panel.getByLabel('Also share this query', { exact: false }).check();
    await panel.getByRole('button', { name: 'Search', exact: true }).click();
    await panel.getByRole('button', { name: 'Not helpful', exact: true }).click();
    await panel.getByText('Feedback was not saved.', { exact: false }).waitFor();
    await panel.getByRole('button', { name: 'Not helpful', exact: true }).click();
    await panel.getByText('Feedback saved.', { exact: true }).waitFor();
    assert.equal(searches[1].mode, 'lexical');
    const attempts = interactions.filter(e => e.event_type === 'search.feedback_submitted');
    assert.equal(attempts.length, 2); assert.equal(attempts[0].event_id, attempts[1].event_id);
    assert.equal(interactions.filter(e => e.event_type === 'search.results_viewed').length, 1);
    await panel.getByLabel('Help measure search performance', { exact: false }).uncheck();
    assert.equal(await panel.getByRole('button', { name: 'Not helpful', exact: true }).isDisabled(), true);
    await panel.getByRole('button', { name: 'Withdraw query sharing' }).click();
    await panel.getByText('Query sharing withdrawn.').waitFor();
    // A delayed response for the old search must not claim the new search was revoked.
    delayWithdrawal = true;
    await panel.getByRole('button', { name: 'Withdraw query sharing', exact: true }).click();
    await withdrawalStarted;
    await panel.getByRole('searchbox').fill('A different query');
    await panel.getByRole('button', { name: 'Search', exact: true }).click();
    await panel.getByText('1 match for “A different query”', { exact: true }).waitFor();
    const oldWithdrawalResponse = page.waitForResponse(response => response.url().endsWith('/sharing'));
    releaseWithdrawal();
    await oldWithdrawalResponse;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await panel.getByText('Query sharing withdrawn.', { exact: true }).count(), 0);
    delayWithdrawal = false;
    await panel.getByRole('button', { name: 'Withdraw all earlier shared queries', exact: true }).click();
    await panel.getByText('All earlier query sharing withdrawn.', { exact: true }).waitFor();
    await panel.getByText('Developer search analytics', { exact: true }).click();
    await panel.getByRole('button', { name: 'Refresh analytics' }).click();
    await panel.getByRole('heading', { name: 'Shared examples needing review' }).waitFor();
    fs.mkdirSync('tmp/kafka-ui', { recursive: true });
    await page.screenshot({ path: 'tmp/kafka-ui/desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'tmp/kafka-ui/mobile.png', fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    console.log('PASS semantic default, keyword mode, opt-in, stable feedback retry, withdrawal, stale withdrawal isolation, bulk withdrawal, dashboard, mobile layout');
    recordPass('search-analytics');
  } catch (error) {
    await captureFailure(browser);
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
