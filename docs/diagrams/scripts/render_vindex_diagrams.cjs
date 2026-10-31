const { chromium } = require('C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const names = ['system-overview', 'processing-stages', 'upload-idempotency', 'retries-and-failures', 'optional-search-and-analytics'];

(async () => {
  const response = await fetch('https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js');
  if (!response.ok) throw new Error('Renderer download: ' + response.status);
  const script = await response.text();
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
    await page.route('**/*', route => route.abort());
    await page.setContent('<main id="preview"></main>');
    await page.addScriptTag({ content: script });
    const source = fs.readFileSync('docs/architecture/system_design.md', 'utf8');
    const diagrams = [...source.matchAll(/```mermaid\r?\n([\s\S]*?)```/g)].map(m => m[1]);
    if (diagrams.length !== names.length) throw new Error('Unexpected diagram count');
    fs.mkdirSync('docs/diagrams', { recursive: true });
    fs.mkdirSync('tmp/system-design-preview', { recursive: true });
    for (let i = 0; i < diagrams.length; i++) {
      const result = await page.evaluate(async ({ code, index }) => {
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral',
          flowchart: { htmlLabels: false, curve: 'basis', nodeSpacing: 30, rankSpacing: 45 },
          themeVariables: { fontFamily: 'Arial', fontSize: '16px' } });
        await mermaid.parse(code);
        const { svg } = await mermaid.render('diagram' + index, code);
        document.getElementById('preview').innerHTML = svg;
        const element = document.querySelector('#preview svg');
        element.style.maxWidth = 'none';
        const box = element.viewBox.baseVal;
        element.style.width = box.width + 'px';
        element.style.height = box.height + 'px';
        return { svg, width: box.width, height: box.height };
      }, { code: diagrams[i], index: i });
      fs.writeFileSync('docs/diagrams/' + names[i] + '.svg', result.svg);
      await page.locator('#preview svg').screenshot({ path: 'tmp/system-design-preview/' + names[i] + '.png' });
      if (i === 0) fs.copyFileSync('tmp/system-design-preview/' + names[i] + '.png', 'docs/diagrams/system-overview.png');
      console.log(JSON.stringify({ diagram: names[i], width: result.width, height: result.height, parsed: true }));
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message, error.cause?.message || ''); process.exitCode = 1; });
