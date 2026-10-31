"""Create a local blinded review queue; entered judgments are never auto-submitted."""
import base64
import json
from pathlib import Path
from benchmarks.context_full_quality import ROOT


def main():
    pool=json.loads((ROOT/'pool.json').read_text())
    audit={r['review_id'] for r in json.loads((ROOT/'independent_audit_pool.json').read_text())}
    # Fixed audit first, then the original randomized full-pool order. No model grades shown.
    pool.sort(key=lambda r:r['review_id'] not in audit)
    encoded=base64.b64encode(json.dumps(pool).encode()).decode()
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VIndex passage review</title><style>
body{font:17px/1.6 system-ui;max-width:940px;margin:30px auto;padding:0 20px;color:#183046;background:#f7f9fb}
article,fieldset{background:white;border:1px solid #ccd7df;border-radius:8px;padding:20px;margin:16px 0}
button{font:inherit;padding:8px 16px;margin:5px;border:1px solid #698197;border-radius:5px;cursor:pointer;background:white}
button.primary{background:#174e70;color:white}label{display:block;margin:8px 0}textarea{width:100%;box-sizing:border-box;font:inherit;min-height:80px}
.note{padding:12px;background:#fff1cd}.muted{font-size:14px;color:#526579}#error{color:#a52b27;font-weight:600}
</style><main><h1>Does this passage answer the query?</h1>
<p class="note">Judge this text alone. 2 means it supplies the requested fact, method or reason; 1 means useful but incomplete;
0 means irrelevant, the wrong task, or only a topic mention. Do not fill gaps using outside knowledge. Choose unsure when needed.</p>
<p>Method, rank and existing labels are hidden. Passage length may reveal whether context was added.
The first40 items are the fixed audit sample; the rest cover the full comparison.</p>
<p id="progress" aria-live="polite"></p><h2 id="query"></h2><article id="passage"></article>
<fieldset><legend>Your assessment</legend><label><input type="radio" name="grade" value="0">0 — Does not answer</label>
<label><input type="radio" name="grade" value="1">1 — Partially answers</label><label><input type="radio" name="grade" value="2">2 — Answers</label>
<label><input type="radio" name="grade" value="unsure">Unsure</label>
<label for="evidence">Supporting excerpt (required for grade2; copy from the passage)</label><textarea id="evidence"></textarea>
<button id="selection">Use selected text</button><label for="reason">Reason or missing information</label><textarea id="reason"></textarea></fieldset>
<p id="error" role="alert"></p><button id="previous">Previous</button><button id="save" class="primary">Save &amp; next</button>
<button id="skip">Skip</button><button id="export">Export entered judgments</button>
<p class="muted">Nothing is sent to a server or written into the benchmark automatically. If browser storage is available, entries are saved locally;
export regularly to retain a file copy. An export records only assessments you entered, not any AI labels.</p></main>
<script>
const records=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob('__DATA__'),c=>c.charCodeAt(0))));
const key='vindex-context-review-v1'; let answers={},index=0;
try{answers=JSON.parse(localStorage.getItem(key)||'{}');}catch(e){}
const $=id=>document.getElementById(id);
function show(){const r=records[index],a=answers[r.review_id]||{};$('query').textContent=r.query;$('passage').textContent=r.text;
$('progress').textContent=`Passage ${index+1} of ${records.length} · ${Object.keys(answers).length} saved`;
document.querySelectorAll('[name=grade]').forEach(n=>n.checked=String(a.rating)===n.value);
$('evidence').value=a.evidence||'';$('reason').value=a.reason||'';$('error').textContent='';}
function move(step){index=Math.max(0,Math.min(records.length-1,index+step));show();window.scrollTo(0,0);}
$('previous').onclick=()=>move(-1);$('skip').onclick=()=>move(1);
$('selection').onclick=()=>{$('evidence').value=window.getSelection().toString();};
$('save').onclick=()=>{const selected=document.querySelector('[name=grade]:checked');if(!selected){$('error').textContent='Choose a grade or unsure.';return;}
const r=records[index],evidence=$('evidence').value.trim();if(selected.value==='2'&&(!evidence||!r.text.includes(evidence))){$('error').textContent='Copy an exact supporting excerpt from this passage.';return;}
answers[r.review_id]={review_id:r.review_id,rating:selected.value==='unsure'?'unsure':Number(selected.value),evidence,reason:$('reason').value,
provenance:'user_entered_local_review',reviewed_at:new Date().toISOString()};
try{localStorage.setItem(key,JSON.stringify(answers));}catch(e){$('error').textContent='Browser storage unavailable. Export before closing.';return;}move(1);};
$('export').onclick=()=>{const data=JSON.stringify({schema:'vindex-context-review-v1',assessments:Object.values(answers)},null,2);
const url=URL.createObjectURL(new Blob([data],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='vindex-context-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
show();
</script></html>'''.replace('__DATA__',encoded)
    (ROOT/'review_queue.html').write_text(html,encoding='utf-8')
    print('Created blinded review queue:',len(pool),'passages; no model grades embedded.')


if __name__=='__main__':main()
