// Render the editable SVG sources locally; never contacts the running application.
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require('../../../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'] });
  try {
    for (const [name, width, height] of [
      ['01-system-flow', 1600, 1050],
      ['02-orchestration', 1800, 970],
      ['03-processing-dag', 1800, 1120],
    ]) {
      const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1.25 });
      await page.setContent(`<style>html,body { margin:0; padding:0; }</style>${fs.readFileSync(path.join(__dirname, `${name}.svg`), 'utf8')}`);
      await page.screenshot({ path: path.join(__dirname, `${name}.png`), fullPage: true, timeout: 60000 });
      const outside = await page.locator('text').evaluateAll(nodes => nodes.filter(node => {
        const box = node.getBBox();
        return box.x < 0 || box.y < 0 || box.x + box.width > node.ownerSVGElement.viewBox.baseVal.width ||
          box.y + box.height > node.ownerSVGElement.viewBox.baseVal.height;
      }).map(node => node.textContent));
      if (outside.length) throw new Error(`${name}: text outside canvas: ${outside.join(', ')}`);
      console.log(`${name}: rendered, all text within canvas`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exit(1); });
