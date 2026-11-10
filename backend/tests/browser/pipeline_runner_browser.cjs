// Exercise the actual CLI and evidence files against a loopback-only fixture server.
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const { recordPass } = require('./harness.cjs');

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vindex-pipeline-cli-'));
  const fixture = path.join(__dirname, 'fixtures/readiness.webm');
  let origin, activeOutput, checkpointBeforeReady = false, searchable = true;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, origin);
    const json = body => { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(body)); };
    if (url.pathname.startsWith('/bench-')) {
      response.setHeader('Content-Type', 'text/html');
      response.end(`<button onclick="document.querySelector('dialog').showModal()">Upload video</button>
        <dialog><input type="file"><label>Title<input type="text"></label><button onclick="upload()">Upload video</button></dialog>
        <script>async function upload() {
          if (location.pathname === '/bench-api') await fetch('/videos/upload', { method:'POST' });
          else { const intent = await (await fetch('/videos/uploads', { method:'POST' })).json();
            await fetch(intent.upload.url, { method:'POST' });
            await fetch('/videos/uploads/video-1/complete', { method:'POST' }); }
        }</script>`);
    } else if (url.pathname === '/videos/uploads') json({ upload: { url: origin + '/storage' } });
    else if (url.pathname === '/storage') { response.statusCode = 204; response.end(); }
    else if (url.pathname === '/videos/upload' || url.pathname.endsWith('/complete')) json({ video_id: 'video-1' });
    else if (url.pathname.endsWith('/search')) {
      if (!searchable && !checkpointBeforeReady) {
        const journal = fs.readFileSync(path.join(activeOutput, 'milestones.jsonl'), 'utf8');
        assert.ok(journal.includes('"milestone":"upload"') && journal.includes('"video_id":"video-1"'));
        assert.equal(fs.existsSync(path.join(activeOutput, 'attempts.jsonl')), false, 'Checkpoint must precede completed attempts');
        assert.equal(fs.existsSync(path.join(activeOutput, 'completion.json')), false, 'CLI must still be running');
        checkpointBeforeReady = true;
      }
      json({ video_id: 'video-1', results: searchable ? [{ text: 'vector search', start: 1, end: 3 }] : [] });
    }
    else if (url.pathname.endsWith('/file-url')) json({ video_id: 'video-1', url: origin + '/media' });
    else if (url.pathname === '/media') { response.setHeader('Content-Type', 'video/webm'); response.end(fs.readFileSync(fixture)); }
    else { response.statusCode = 404; response.end(); }
  });
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = `http://127.0.0.1:${server.address().port}`;
    for (const mode of ['api', 'direct']) {
      // One full success and one accepted-but-search-timeout exercise exit/completion separation.
      searchable = mode === 'api';
      const manifest = { environment: 'staging', commit: 'a'.repeat(40),
        targets: { api: origin + '/bench-api', direct: origin + '/bench-direct' }, files: [fixture],
        repetitions: 1, concurrency: 1, timeout_seconds: 5, network: { name: 'unshaped' },
        pipeline: { mode, worker_state: 'warm', deadline_seconds: 2, poll_interval_ms: 100, probe_timeout_ms: 1000,
          search: { query: 'vector search', expected_text: 'vector search', start_seconds: 1, end_seconds: 3, tolerance_seconds: 0 },
          preflight_file: path.join(directory, 'preflight.json') } };
      fs.writeFileSync(manifest.pipeline.preflight_file, JSON.stringify({ isolated: true, queue_empty: true,
        commit: manifest.commit, target: manifest.targets[mode], worker_state: 'warm', observed_at: new Date().toISOString(),
        ...Object.fromEntries(['media_bucket', 'database', 'redis', 'qdrant', 'worker_identity', 'capacity', 'model_versions', 'cleanup_plan'].map(k => [k, 'loopback fixture; no services'])) }));
      const file = path.join(directory, 'manifest.json'), output = path.join(directory, mode);
      activeOutput = output;
      fs.writeFileSync(file, JSON.stringify(manifest));
      const child = spawn(process.execPath, [path.join(__dirname, '../../benchmarks/run_upload_comparison.cjs'), '--manifest', file, '--output', output],
        { env: process.env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      let logs = '';
      child.stdout.on('data', chunk => { logs += chunk; }); child.stderr.on('data', chunk => { logs += chunk; });
      let watchdog;
      const code = await new Promise((resolve, reject) => {
        watchdog = setTimeout(() => { child.kill(); reject(new Error('CLI fixture exceeded its deadline')); }, 20000);
        child.on('error', reject); child.on('close', resolve);
      }).finally(() => clearTimeout(watchdog));
      assert.equal(code, searchable ? 0 : 1, logs);
      const read = name => JSON.parse(fs.readFileSync(path.join(output, name), 'utf8'));
      const completion = read('completion.json');
      assert.equal(completion.finished_all_planned_waves, true);
      assert.equal(completion.planned_attempts, 1);
      assert.equal(completion.all_attempts_accepted, true);
      assert.equal(completion.all_attempts_ready, searchable);
      assert.equal(read('run.json').readiness.worker_state, 'warm');
      assert.equal(read('fixtures.jsonl').sha256.length, 64);
      const attempts = fs.readFileSync(path.join(output, 'attempts.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
      assert.equal(attempts.length, 1);
      assert.equal(attempts[0].playable.outcome, 'ready');
      assert.equal(attempts[0].searchable.outcome, searchable ? 'ready' : 'timeout');
      assert.equal(Object.values(read('summary.json'))[0].pipeline.both_ready, searchable ? 1 : 0);
      assert.ok(fs.readFileSync(path.join(output, 'milestones.jsonl'), 'utf8').includes('"milestone":"upload"'));
    }
    assert.equal(checkpointBeforeReady, true);
    recordPass('pipeline-runner');
    console.log('Pipeline CLI fixture checks passed');
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(directory, { recursive: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
