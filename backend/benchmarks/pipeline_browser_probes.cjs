// Runs fetch/media inside the measured browser, so its network shaping applies.
// Tokens, signed URLs, transcript text and response bodies never leave the page.
async function browserProbe({ kind, videoId, timeoutMs, search }) {
  const controller = new AbortController();
  const began = performance.now();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const session = JSON.parse(localStorage.getItem('vindex:authSession') || 'null');
    const headers = { 'Content-Type': 'application/json' };
    if (session?.idToken) headers.Authorization = `Bearer ${session.idToken}`;
    const root = `/videos/${encodeURIComponent(videoId)}`;
    const response = await fetch(root + (kind === 'searchable' ? '/search' : '/file-url'), {
      method: kind === 'searchable' ? 'POST' : 'GET', headers, credentials: 'same-origin', cache: 'no-store',
      redirect: 'error', signal: controller.signal,
      ...(kind === 'searchable' ? { body: JSON.stringify({ query: search.query, algorithm: 'qdrant_embedding' }) } : {})
    });
    if (!response.ok) return { ready: false, code: `http_${response.status}`,
      terminal: ![400, 404, 409, 429, 500, 502, 503, 504].includes(response.status) };
    const body = await response.json();
    if (body.video_id !== videoId) return { ready: false, terminal: true, code: 'identity_mismatch' };
    if (kind === 'searchable') {
      const normalize = text => text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
      const phrase = normalize(search.expected_text);
      const match = phrase && Array.isArray(body.results) && body.results.some(hit =>
        typeof hit.text === 'string' && ` ${normalize(hit.text)} `.includes(` ${phrase} `) &&
        Number.isFinite(hit.start) && Number.isFinite(hit.end) && hit.start >= 0 && hit.end > hit.start &&
        hit.start <= search.end_seconds + search.tolerance_seconds && hit.end >= search.start_seconds - search.tolerance_seconds);
      return { ready: !!match, code: match ? 'expected_search_hit' : 'expected_hit_missing' };
    }
    const url = new URL(body.url);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
      return { ready: false, terminal: true, code: 'invalid_media_url' };
    }
    const remaining = timeoutMs - (performance.now() - began);
    if (remaining <= 0) return { ready: false, code: 'probe_timeout' };
    return await new Promise(resolve => {
      const video = document.createElement('video');
      video.muted = true; video.playsInline = true; video.preload = 'auto';
      // Keep the probe composited so frame callbacks work in headless Chromium.
      video.style.cssText = 'position:fixed;bottom:0;right:0;width:2px;height:2px;pointer-events:none';
      video.dataset.vindexPipelineProbe = 'true';
      let finished = false, frame;
      const finish = result => {
        if (finished) return;
        finished = true;
        clearTimeout(mediaTimer);
        if (frame !== undefined) video.cancelVideoFrameCallback(frame);
        video.pause(); video.removeAttribute('src'); video.load(); video.remove();
        resolve(result);
      };
      const mediaTimer = setTimeout(() => finish({ ready: false, code: 'decode_timeout' }), remaining);
      video.onerror = () => finish({ ready: false, code: 'decode_error' });
      if (!video.requestVideoFrameCallback) return finish({ ready: false, terminal: true, code: 'frame_callback_unavailable' });
      const onFrame = (_, metadata) => {
        if (video.videoWidth > 0 && video.videoHeight > 0 && metadata.mediaTime >= 0.1 && metadata.presentedFrames > 0) {
          finish({ ready: true, code: 'decoded_advancing_video' });
        } else if (!finished) frame = video.requestVideoFrameCallback(onFrame);
      };
      document.body.appendChild(video);
      frame = video.requestVideoFrameCallback(onFrame);
      video.src = url.href; // No API authorization header is forwarded to storage.
      video.play().catch(() => finish({ ready: false, code: 'play_rejected' }));
    });
  } catch { return { ready: false, code: controller.signal.aborted ? 'probe_timeout' : 'probe_error' }; }
  finally { clearTimeout(timer); }
}

function createBrowserProbes(page) {
  return Object.fromEntries(['searchable', 'playable'].map(kind => [kind, args => page.evaluate(browserProbe, { kind, ...args })]));
}
module.exports = { browserProbe, createBrowserProbes };
