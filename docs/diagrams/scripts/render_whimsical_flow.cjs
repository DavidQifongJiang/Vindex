const { chromium } = require('C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');

(async () => {
  const response = await fetch('https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js');
  if (!response.ok) throw new Error('Renderer download failed: ' + response.status);
  const script = await response.text();
  const code = fs.readFileSync('docs/diagrams/vindex-whimsical-flow.mmd', 'utf8');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1400 }, deviceScaleFactor: 2 });
    await page.route('**/*', route => route.abort());
    await page.setContent('<style>body { margin: 24px; background: #fff; } main { display: inline-block; padding: 12px; }</style><main id="preview"></main>');
    await page.addScriptTag({ content: script });
    const result = await page.evaluate(async (source) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        flowchart: { htmlLabels: true, curve: 'basis', nodeSpacing: 42, rankSpacing: 30, padding: 15 },
        themeVariables: { fontFamily: 'Arial', fontSize: '16px', primaryTextColor: '#24364b', lineColor: '#7f90a2', clusterBkg: '#f9fbfd', clusterBorder: '#d9e3ec', edgeLabelBackground: '#ffffff' }
      });
      await mermaid.parse(source);
      const { svg } = await mermaid.render('vindexWhimsicalFlow', source);
      document.getElementById('preview').innerHTML = svg;
      const element = document.querySelector('#preview svg');
      const box = element.viewBox.baseVal;
      element.style.maxWidth = 'none';
      element.style.width = box.width + 'px';
      element.style.height = box.height + 'px';
      return { svg, width: box.width, height: box.height, nodes: document.querySelectorAll('.node').length };
    }, code);
    fs.writeFileSync('docs/diagrams/vindex-whimsical-flow.svg', result.svg);
    await page.locator('#preview').screenshot({ path: 'docs/diagrams/vindex-whimsical-flow.png' });
    console.log(JSON.stringify({ parsed: true, width: result.width, height: result.height, nodes: result.nodes }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message, error.cause?.message || ''); process.exitCode = 1; });
