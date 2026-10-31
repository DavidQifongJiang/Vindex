/* Authoring QA only; the guide itself has no external JavaScript dependencies. */
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1400,height:1050}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.copiedTutorialCode=value}}});
    });
    await page.goto(pathToFileURL(path.join(__dirname,'index.html')).href);
    if(await page.locator('article').count()!==17)throw new Error('Missing sections');
    await page.locator('.lesson[data-index="2"]').click();
    if(!/1 Run something/.test(await page.locator('article:not([hidden]) h1').textContent()))throw new Error('Navigation failed');
    const expected=await page.locator('article:not([hidden]) pre code').first().textContent();
    await page.locator('article:not([hidden]) .copy').first().click();
    if(await page.evaluate(()=>window.copiedTutorialCode)!==expected)throw new Error('Copy content mismatch');
    await page.locator('#completed').check();
    await page.reload();
    if(!await page.locator('#completed').isChecked())throw new Error('Progress did not persist');
    await page.locator('#completed').uncheck();
    await page.evaluate(()=>document.getElementById('status').textContent='');
    await page.screenshot({path:path.join(__dirname,'preview.png')});
    await page.setViewportSize({width:390,height:844});
    for(let i=0;i<17;i++){
      await page.locator(`.lesson[data-index="${i}"]`).click();
      if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw new Error('Page overflow in section '+i);
    }
    if(errors.length)throw new Error(errors.join('\n'));
    const validation=JSON.parse(fs.readFileSync(path.join(__dirname,'validation.json'),'utf8'));
    validation.checks.guide='17 sections checked at 390px; navigation, exact copy, saved progress and desktop preview passed';
    fs.writeFileSync(path.join(__dirname,'validation.json'),JSON.stringify(validation,null,2)+'\n');
    console.log(validation.checks.guide);
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
