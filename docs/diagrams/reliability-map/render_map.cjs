/* Parse every Whimsical source, render vector previews, and build a local guide. */
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = __dirname;
const repo = path.resolve(root, '../../..');
const panels = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
const esc = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

async function main() {
  const cache = path.join(repo, 'tmp', 'vindex-reliability-assets');
  fs.mkdirSync(cache, { recursive: true });
  const bundlePath = path.join(cache, 'mermaid-11.12.0.min.js');
  if (!fs.existsSync(bundlePath)) {
    const response = await fetch('https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js');
    if (!response.ok) throw new Error('Renderer download: ' + response.status);
    fs.writeFileSync(bundlePath, await response.text());
  }
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const results = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1800, height: 1400 }, deviceScaleFactor: 1.5 });
    await page.route(/^https?:/, route => route.abort());
    await page.setContent('<style>body{margin:0;background:white}#render{display:inline-block;padding:30px;background:white}</style><main id="render"></main>');
    await page.addScriptTag({ content: fs.readFileSync(bundlePath, 'utf8') });
    await page.evaluate(() => mermaid.initialize({
      startOnLoad: false, securityLevel: 'strict', theme: 'base', htmlLabels: false,
      flowchart: { htmlLabels: false, curve: 'basis', nodeSpacing: 36, rankSpacing: 44, padding: 16, wrappingWidth: 310 },
      themeVariables: { fontFamily: 'Arial', fontSize: '17px', primaryTextColor: '#24364b', lineColor: '#7c8a9b',
        clusterBkg: '#f7f9fc', clusterBorder: '#d2dce6', edgeLabelBackground: '#ffffff' }
    }));
    for (let index = 0; index < panels.length; index++) {
      const panel = panels[index];
      const code = fs.readFileSync(path.join(root, panel.file), 'utf8');
      const result = await page.evaluate(async ({ code, index }) => {
        await mermaid.parse(code);
        const { svg } = await mermaid.render('reliabilityPanel' + index, code);
        document.getElementById('render').innerHTML = svg;
        const element = document.querySelector('#render > svg');
        element.style.maxWidth = 'none';
        const bounds = element.viewBox.baseVal;
        element.setAttribute('width', bounds.width);
        element.setAttribute('height', bounds.height);
        element.setAttribute('role', 'img');
        const escapedText = [...element.querySelectorAll('text')].filter(text => /&(gt|lt|amp);/.test(text.textContent)).map(text => text.textContent);
        const clipped = [...element.querySelectorAll('g.node')].filter(node => {
          const box = node.getBoundingClientRect();
          const outer = element.getBoundingClientRect();
          return box.left < outer.left - 2 || box.right > outer.right + 2 || box.top < outer.top - 2 || box.bottom > outer.bottom + 2;
        }).map(node => node.id);
        return { svg: element.outerHTML, width: bounds.width, height: bounds.height,
          nodes: element.querySelectorAll('g.node').length, nativeText: element.querySelectorAll('text').length,
          foreignObjects: element.querySelectorAll('foreignObject').length, clipped, escapedText };
      }, { code, index });
      if (result.clipped.length) throw new Error(panel.slug + ': clipped nodes ' + result.clipped.join(', '));
      if (result.escapedText.length) throw new Error(panel.slug + ': escaped label text ' + result.escapedText.join(', '));
      fs.writeFileSync(path.join(root, panel.slug + '.svg'), result.svg);
      await page.locator('#render').screenshot({ path: path.join(root, panel.slug + '.png') });
      const { svg, ...metadata } = result;
      panel.render = metadata;
      panel.svg = svg;
      results.push({ panel: panel.slug, parsed: true, ...metadata });
      console.log(JSON.stringify(results.at(-1)));
    }
    const all = fs.readFileSync(path.join(root, 'vindex-reliability-all.mmd'), 'utf8');
    await page.evaluate(async code => { await mermaid.parse(code); }, all);
    buildPoster();
    buildGuide();
    await verifyGuide(browser);
    fs.writeFileSync(path.join(root, 'validation.json'), JSON.stringify({
      checked: 'Mermaid syntax; individual rendering; node bounds; guide navigation and narrow layout',
      combinedParsed: true, panelCount: panels.length, nodeCount: results.reduce((n, p) => n + p.nodes, 0),
      remoteWhimsicalImportVerified: false, backendTestsRun: false, panels: results
    }, null, 2) + '\n');
    console.log('Combined source parsed; guide and full vector poster created; guide checks passed.');
  } finally {
    await browser.close();
  }
}

function buildPoster() {
  const columnWidth = 1400, gap = 48, margin = 60, titleHeight = 100;
  const totalWidth = margin * 2 + columnWidth * 4 + gap * 3;
  const placements = [];
  let y = 180;
  for (let row = 0; row < 4; row++) {
    const batch = panels.slice(1 + row * 4, 5 + row * 4);
    let rowHeight = 0;
    batch.forEach((panel, column) => {
      const scale = Math.min(1, (columnWidth - 48) / panel.render.width);
      const height = panel.render.height * scale;
      rowHeight = Math.max(rowHeight, height + titleHeight + 36);
      placements.push({ panel, x: margin + column * (columnWidth + gap), y, width: panel.render.width * scale, height });
    });
    y += rowHeight + gap;
  }
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" width="${totalWidth}" height="${y + margin}" viewBox="0 0 ${totalWidth} ${y + margin}">`,
    '<title>Vindex complete reliability map</title>',
    '<desc>Sixteen numbered detail sections cover core upload and jobs, optional indexing and analytics, metrics, backups, and guarantee boundaries. Open the individual sections in the accompanying guide to read the full notes and copy editable Whimsical source.</desc>',
    `<rect width="100%" height="100%" fill="#f3f6fa"/>`,
    `<text x="${margin}" y="75" font-family="Arial" font-size="42" fill="#172f4a">Vindex · Reliability, retries and idempotency</text>`,
    `<text x="${margin}" y="120" font-family="Arial" font-size="24" fill="#536578">Working-tree implementation · October 26, 2026 · Optional systems labeled · Defaults are not measured recovery times</text>`];
  for (const { panel, x, y, width, height } of placements) {
    parts.push(`<rect x="${x}" y="${y}" width="${columnWidth}" height="${height + titleHeight + 36}" rx="12" fill="white" stroke="#d7e0e9"/>`);
    parts.push(`<text x="${x + 24}" y="${y + 40}" font-family="Arial" font-size="27" fill="#172f4a">${esc(panel.title)}</text>`);
    parts.push(`<text x="${x + 24}" y="${y + 72}" font-family="Arial" font-size="20" fill="#66748a">${esc(panel.scope)}</text>`);
    const encoded = Buffer.from(panel.svg).toString('base64');
    parts.push(`<image x="${x + (columnWidth - width) / 2}" y="${y + titleHeight}" width="${width}" height="${height}" href="data:image/svg+xml;base64,${encoded}"/>`);
  }
  parts.push('</svg>');
  fs.writeFileSync(path.join(root, 'vindex-reliability-board.svg'), parts.join('\n'));
}

function buildGuide() {
  const nav = panels.map((p, i) => `<button type="button" class="section-link${i === 0 ? ' active' : ''}" data-panel="${i}" aria-current="${i === 0 ? 'page' : 'false'}">${esc(p.title)}</button>`).join('\n');
  const content = panels.map((p, i) => `<section id="panel-${i}" ${i ? 'hidden' : ''}>
    <p class="scope">${esc(p.scope)}</p><h2>${esc(p.title)}</h2>
    <div class="actions"><button type="button" data-copy="${i}">Copy for Whimsical</button><button type="button" data-size="${i}" aria-pressed="false">100% size</button><a href="${p.file}">Source</a><a href="${p.slug}.svg">Full-size vector</a></div>
    <p class="notes">${esc(p.notes)}</p>
    <div class="diagram">${p.svg}</div>
    <details><summary>Implementation and existing test references</summary><ul>${[...p.sources, ...p.tests.filter(t => fs.existsSync(path.join(repo, t)))].map(s => `<li><a href="../../../${s}">${esc(s)}</a></li>`).join('')}</ul></details>
    <details class="source"><summary>Show copy-ready Mermaid text</summary><textarea readonly aria-label="${esc(p.title)} Mermaid source">${esc(fs.readFileSync(path.join(root, p.file), 'utf8'))}</textarea></details>
  </section>`).join('\n');
  const sourceJson = JSON.stringify(panels.map(p => fs.readFileSync(path.join(root, p.file), 'utf8'))).replace(/</g, '\\u003c');
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vindex reliability map</title>
  <style>
  *{box-sizing:border-box}body{margin:0;background:#f4f7fb;color:#20344b;font:16px/1.5 Arial,sans-serif}header{padding:28px 32px;background:#142b43;color:#f5f8fc}h1{margin:0;font-size:27px}header p{margin:6px 0 16px;color:#c5d5e7}header a{color:#e7f0ff}header .links{display:flex;flex-wrap:wrap;gap:20px}a{color:#245e9b}button{font:inherit;cursor:pointer}main{display:grid;grid-template-columns:292px minmax(0,1fr);max-width:1600px;margin:auto}nav{padding:22px 16px;align-self:start;position:sticky;top:0;max-height:100vh;overflow-y:auto}.section-link{display:block;text-align:left;border:0;background:transparent;padding:10px 12px;border-radius:6px;width:100%;color:#3a4c63;font-size:14px}.section-link:hover{background:#e4ebf4}.section-link.active{background:#dbe8f7;color:#183d66;font-weight:bold}.content{padding:28px 32px;min-width:0}section{background:#fff;padding:28px;border:1px solid #dce4ed;border-radius:10px}h2{font-size:24px;line-height:1.3;margin:4px 0 18px}.scope{color:#6b568d;font-size:13px;font-weight:bold;margin:0}.actions{display:flex;gap:20px;align-items:center;flex-wrap:wrap}.actions button{border:0;border-radius:6px;background:#235e9d;color:white;padding:10px 16px}.notes{max-width:950px;color:#52647a;margin:22px 0}.diagram{overflow:auto;border-top:1px solid #edf0f5;padding:24px 0}.diagram>svg{display:block;width:100%;height:auto;min-width:600px;max-width:none!important;margin:auto}details{border-top:1px solid #e5ebf1;padding:16px 0}summary{cursor:pointer;color:#245e9b}textarea{width:100%;min-height:350px;margin-top:16px;padding:14px;border:1px solid #b6c7da;border-radius:5px;font:13px/1.5 Consolas,monospace;color:#20344b;background:#f8fbff}#status{min-height:28px;margin:12px 0 0;color:#c5d5e7}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #e7a137;outline-offset:3px}@media(max-width:800px){header{padding:24px 18px}main{display:block}nav{position:static;max-height:280px;border-bottom:1px solid #dce4ed;display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.content{padding:16px}section{padding:18px}.diagram>svg{min-width:600px}h2{font-size:21px}.section-link{font-size:13px}}@media(max-width:420px){nav{grid-template-columns:1fr}}
  </style></head><body><header><h1>Vindex reliability map</h1><p>17 readable sections · Source audit: October 26, 2026 · Core, optional and legacy paths distinguished</p>
  <div class="links"><a href="vindex-reliability-board.svg">Open the complete vector board</a><a href="vindex-reliability-all.mmd">All sections in one Mermaid source</a><a href="README.md">Source and test traceability</a></div>
  <p id="status" role="status">Choose a section, copy its source, then Ctrl+V on an empty area of your Whimsical board.</p></header>
  <main><nav aria-label="Reliability sections">${nav}</nav><div class="content">${content}</div></main>
  <script>
  const sources=${sourceJson};
  function selectPanel(index){document.querySelectorAll('section').forEach((s,i)=>s.hidden=i!==index);document.querySelectorAll('[data-panel]').forEach((b,i)=>{b.classList.toggle('active',i===index);b.setAttribute('aria-current',i===index?'page':'false')});history.replaceState(null,'','#'+index)}
  document.querySelectorAll('[data-panel]').forEach(b=>b.addEventListener('click',()=>selectPanel(Number(b.dataset.panel))));
  document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',async()=>{const index=Number(b.dataset.copy);try{await navigator.clipboard.writeText(sources[index]);document.getElementById('status').textContent='Copied. In Whimsical, click an empty canvas area and press Ctrl+V.'}catch{const section=document.getElementById('panel-'+index);section.querySelector('details.source').open=true;const area=section.querySelector('textarea');area.focus();area.select();document.getElementById('status').textContent='Source selected. Press Ctrl+C, then paste onto your Whimsical board.'}}));
  document.querySelectorAll('[data-size]').forEach(b=>b.addEventListener('click',()=>{const svg=document.querySelector('#panel-'+b.dataset.size+' .diagram>svg');const actual=b.getAttribute('aria-pressed')!=='true';svg.style.width=actual?svg.getAttribute('width')+'px':'100%';svg.style.minWidth=actual?svg.getAttribute('width')+'px':'600px';b.setAttribute('aria-pressed',String(actual));b.textContent=actual?'Fit diagram':'100% size'}));
  const initial=Number(location.hash.slice(1));if(Number.isInteger(initial)&&initial>=0&&initial<sources.length)selectPanel(initial);
  </script></body></html>`;
  fs.writeFileSync(path.join(root, 'index.html'), html);
}

async function verifyGuide(browser) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1050 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async value => { window.copiedDiagram = value; } } }); });
  await page.goto(pathToFileURL(path.join(root, 'index.html')).href);
  await page.locator('[data-panel="5"]').click();
  if (await page.locator('section:not([hidden])').getAttribute('id') !== 'panel-5') throw new Error('Guide navigation failed');
  await page.locator('[data-copy="5"]').click();
  if (!/Copied|Source selected/.test(await page.locator('#status').textContent())) throw new Error('Guide copy interaction failed');
  const copied = await page.evaluate(() => window.copiedDiagram);
  if (copied !== fs.readFileSync(path.join(root, panels[5].file), 'utf8')) throw new Error('Wrong diagram copied');
  await page.locator('[data-size="5"]').click();
  if (await page.locator('[data-size="5"]').getAttribute('aria-pressed') !== 'true') throw new Error('Actual-size toggle failed');
  await page.locator('[data-size="5"]').click();
  await page.locator('[data-panel="0"]').click();
  await page.evaluate(() => { document.getElementById('status').textContent='Choose a section, copy its source, then Ctrl+V on an empty area of your Whimsical board.'; });
  await page.screenshot({ path: path.join(root, 'guide-preview.png'), fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error('Guide has page-level narrow-screen overflow');
  if (errors.length) throw new Error('Guide errors: ' + errors.join(', '));
  await page.setViewportSize({ width: 1800, height: 1300 });
  const posterData = fs.readFileSync(path.join(root, 'vindex-reliability-board.svg')).toString('base64');
  await page.setContent('<style>body{margin:0}img{display:block;width:1750px;height:auto}</style><img alt="Vindex complete reliability board" src="data:image/svg+xml;base64,' + posterData + '">');
  await page.locator('img').evaluate(image => image.decode());
  await page.locator('img').screenshot({ path: path.join(root, 'board-preview.png') });
  await page.close();
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
