"""Render the course as a local, searchable guide. Authoring dependency: Markdown."""
import html
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import markdown


ROOT = Path(__file__).resolve().parent


def main():
    source = (ROOT / "TUTORIAL.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (.+)$", source, flags=re.MULTILINE)
    sections = [("Start here", parts[0])]
    sections.extend((parts[i], parts[i + 1]) for i in range(1, len(parts), 2))
    buttons, articles = [], []
    for index, (title, body) in enumerate(sections):
        buttons.append(f'<button type="button" class="lesson{" selected" if index == 0 else ""}" data-index="{index}" aria-current="{"page" if index == 0 else "false"}">{html.escape(title)}</button>')
        rendered = markdown.markdown(body, extensions=["extra", "sane_lists"])
        if index:
            rendered = f'<h1>{html.escape(title)}</h1>' + rendered
        articles.append(f'<article data-lesson="{index}" {"hidden" if index else ""}>{rendered}</article>')
    template = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hands-on CI/CD tutorial for Vindex</title>
<style>
:root{color-scheme:light dark;--bg:#f5f7fb;--text:#23364b;--panel:#fff;--muted:#506276;--line:#dce3ee;--accent:#1f5c97;--selected:#deebfa;--code:#eef3f9}
@media(prefers-color-scheme:dark){:root{--bg:#111a24;--text:#e5edf5;--panel:#182332;--muted:#b5c4d4;--line:#334357;--accent:#9fc8ff;--selected:#2a405c;--code:#101a26}}
*{box-sizing:border-box}body{margin:0;font:16px/1.65 system-ui,sans-serif;background:var(--bg);color:var(--text)}header{padding:24px 32px;border-bottom:1px solid var(--line)}header strong{font-size:23px}header p{margin:5px 0 10px;color:var(--muted)}a{color:var(--accent)}.header-links{display:flex;gap:20px;flex-wrap:wrap;font-size:14px}.layout{display:grid;grid-template-columns:280px minmax(0,1fr);max-width:1450px;margin:auto}nav{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;padding:22px 16px}.lesson{display:block;width:100%;text-align:left;padding:10px 12px;border:0;background:transparent;color:var(--text);font:inherit;font-size:14px;border-radius:5px;cursor:pointer}.lesson.selected{background:var(--selected);font-weight:650}.lesson.done::after{content:' ✓';color:var(--accent)}main{min-width:0;padding:28px 36px 60px}article{background:var(--panel);padding:30px 38px;border:1px solid var(--line);border-radius:8px}h1{font-size:28px;line-height:1.3;margin:0 0 24px}p{margin:16px 0}li{margin:7px 0}code{font:14px/1.5 Consolas,monospace;background:var(--code);padding:2px 4px;border-radius:3px}pre{position:relative;background:var(--code);padding:43px 18px 18px;overflow:auto;border-radius:5px;line-height:1.5}pre code{padding:0;background:transparent;font-size:14px}button{font:inherit;cursor:pointer}.copy{position:absolute;right:8px;top:6px;border:1px solid var(--line);background:var(--panel);color:var(--accent);border-radius:4px;padding:3px 10px;font-size:12px}table{border-collapse:collapse;width:100%;margin:20px 0;table-layout:fixed}th,td{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:var(--code)}details{padding:16px 0}summary{color:var(--accent);cursor:pointer}.pager{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:24px}.pager button{padding:8px 15px;border:1px solid var(--line);border-radius:5px;background:var(--panel);color:var(--accent)}.pager button:disabled{opacity:.45;cursor:default}.pager label{margin-left:auto;font-size:14px}#status{min-height:26px;color:var(--muted);font-size:14px}#progress{font-size:13px;color:var(--muted);padding:0 12px 12px}button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
@media(max-width:850px){.layout{grid-template-columns:1fr}nav{position:static;max-height:250px;border-bottom:1px solid var(--line)}main{padding:18px}article{padding:22px}h1{font-size:24px}header{padding:22px}th,td{padding:7px;font-size:14px}}@media(max-width:450px){article{padding:16px}main{padding:10px}pre{padding-left:10px;padding-right:10px}.pager label{margin-left:0;width:100%}}
</style></head><body>
<header><strong>Build, break, deploy, recover</strong><p>A hands-on CI/CD course using a video chunk planner · Local Docker first · Optional EC2</p><div class="header-links"><a href="TUTORIAL.md">Full tutorial source</a><a href="starter.zip">Practice starter ZIP</a><a href="starter/solutions/pipeline.yml">Complete pipeline example</a><a href="validation.json">Local validation record</a></div></header>
<div class="layout"><nav aria-label="Course lessons"><div id="progress"></div>__NAV__</nav><main>__ARTICLES__<div class="pager"><button type="button" id="previous">Previous</button><button type="button" id="next">Next lesson</button><label><input type="checkbox" id="completed"> I completed this checkpoint</label></div><p id="status" role="status" aria-live="polite"></p></main></div>
<script>
const articles=[...document.querySelectorAll('article')];
const lessons=[...document.querySelectorAll('.lesson')];
const progressKey='vindex-cicd-course-progress-v1';
let completed=[];try{const saved=JSON.parse(localStorage.getItem(progressKey)||'[]');completed=Array.isArray(saved)?saved:[]}catch{}
let current=0;
function update(){lessons.forEach((button,i)=>{button.classList.toggle('selected',i===current);button.classList.toggle('done',completed.includes(i));button.setAttribute('aria-current',i===current?'page':'false')});articles.forEach((article,i)=>article.hidden=i!==current);document.getElementById('previous').disabled=current===0;document.getElementById('next').disabled=current===articles.length-1;document.getElementById('completed').checked=completed.includes(current);document.getElementById('progress').textContent=completed.length+' / '+articles.length+' sections checked';}
function select(index,scroll=true){if(index<0||index>=articles.length)return;current=index;update();history.replaceState(null,'','#'+current);if(scroll)window.scrollTo(0,0)}
lessons.forEach((button,i)=>button.addEventListener('click',()=>select(i)));
document.getElementById('previous').addEventListener('click',()=>select(current-1));
document.getElementById('next').addEventListener('click',()=>select(current+1));
document.getElementById('completed').addEventListener('change',event=>{completed=completed.filter(i=>i!==current);if(event.target.checked)completed.push(current);try{localStorage.setItem(progressKey,JSON.stringify(completed));document.getElementById('status').textContent='Progress saved in this browser.'}catch{document.getElementById('status').textContent='Progress updated for this session; browser storage is unavailable.'}update()});
document.querySelectorAll('pre').forEach(pre=>{const button=document.createElement('button');button.type='button';button.className='copy';button.textContent='Copy';button.setAttribute('aria-label','Copy code block');button.addEventListener('click',async()=>{const code=pre.querySelector('code');try{await navigator.clipboard.writeText(code.textContent);document.getElementById('status').textContent='Copied code block.'}catch{const range=document.createRange();range.selectNodeContents(code);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.getElementById('status').textContent='Code selected. Press Ctrl+C to copy.'}});pre.appendChild(button)});
const initial=Number(location.hash.slice(1));select(Number.isInteger(initial)&&initial>=0&&initial<articles.length?initial:0,false);
</script></body></html>'''
    (ROOT / "index.html").write_text(template.replace("__NAV__", "\n".join(buttons)).replace("__ARTICLES__", "\n".join(articles)), encoding="utf-8")
    with ZipFile(ROOT / "starter.zip", "w", ZIP_DEFLATED) as archive:
        for file in sorted((ROOT / "starter").rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
                archive.write(file, file.relative_to(ROOT / "starter"))
    print(f"Built {len(sections)} guide sections and starter.zip")


if __name__ == "__main__":
    main()
