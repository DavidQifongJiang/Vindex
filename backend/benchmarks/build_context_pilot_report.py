"""Build a local, dependency-free before/after inspection page for the pilot."""
import json
from html import escape
from pathlib import Path
from benchmarks.context_quality_pilot import OUTPUT, BASE


def main():
    summary=json.loads((OUTPUT/'summary.json').read_text())
    scores=json.loads((OUTPUT/'scores.json').read_text())
    labels={r['review_id']:r for r in json.loads((OUTPUT/'assessments.json').read_text())}
    rows={r['query_id']:r for r in json.loads((BASE/'context-full500-01/results.json').read_text())
          if r['method']=='hybrid'}
    parts=['''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VIndex context pilot — before and after</title>
<style>body{font:16px/1.6 system-ui;margin:32px auto;padding:0 24px;max-width:1180px;color:#172a3a;background:#f6f8fa}
h1,h2{line-height:1.2}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #ddd}
.note{padding:16px;background:#fff4d7;border-left:4px solid #c7891a}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}
article{background:white;padding:18px;border:1px solid #dce3e9;border-radius:8px}summary{cursor:pointer;font-weight:600;padding:12px}
details.query{margin:20px 0;background:#edf2f6;border-radius:8px}section{padding:16px}mark{background:#fff0a4;color:inherit}
.meta{font-size:14px;color:#506172}blockquote{border-left:3px solid #a3b5c3;padding-left:12px;margin-left:0}
@media(max-width:740px){.pair{grid-template-columns:1fr}body{padding:0 12px;margin:20px auto}}
</style><h1>Does neighboring context complete the answer?</h1>
<p class="note">Exploratory pilot: 10 selected development queries, 98 separately reviewed passages.
All grades are AI assessments, not human judgments. Rankings stay unchanged. The right-hand condition assumes
the user opens context; the collapsed search result still has the original quality. This page is a saved
benchmark preview, not the deployed product. Yellow text marks the unchanged original passage.</p>
<table><tr><th>Condition</th><th>Hit@1</th><th>Hit@5</th><th>MRR@5</th></tr>''']
    for c,label in [('original','Original passage'),('opened_context_120','Opened context (up to120s)')]:
        m=summary['methods'][c]
        parts.append(f"<tr><td>{label}</td><td>{m['hit_at_1']['value']:.0%}</td><td>{m['hit_at_5']['value']:.0%}</td><td>{m['mrr_at_5']['value']:.3f}</td></tr>")
    parts.append('</table><p>Two queries gained an answer in the top five; none lost one. '
                 'The paired 95% bootstrap interval for the Hit@5 difference spans 0 to50 percentage points. '
                 'This small selected pilot does not establish a general improvement.</p>')
    for qid in sorted({r['query_id'] for r in scores}):
        raw=rows[qid]
        methods={r['condition']:r for r in scores if r['query_id']==qid}
        parts.append(f'<details class="query"><summary>{escape(raw["query"])}</summary><section>')
        for rank,(old,new) in enumerate(zip(raw['original_results'],raw['results']),1):
            anchor=old['text']
            if anchor not in new['text']:raise ValueError('Original anchor missing')
            before,after=new['text'].split(anchor,1)
            expanded=escape(before)+'<mark>'+escape(anchor)+'</mark>'+escape(after)
            parts.append(f'<h2>Result {rank}</h2><p class="meta">Playback anchor remains {old["start"]:.2f}s; '
                         f'context spans {new["start"]:.2f}–{new["end"]:.2f}s.</p><div class="pair">')
            for condition,title,text in [('original','Original',escape(anchor)),('opened_context_120','With neighboring context',expanded)]:
                grade=labels[methods[condition]['review_ids'][rank-1]]
                parts.append(f'<article><h3>{title}</h3><p>{text}</p><details><summary>Show AI assessment</summary>'
                             f'<p>Grade {grade["rating"]}/2 — {escape(grade["rationale"])}</p>'
                             f'<blockquote>{escape(grade["evidence"])}</blockquote></details></article>')
            parts.append('</div>')
        parts.append('</section></details>')
    parts.append('<p>Selection excluded previously reviewed queries, including all47 previously labeled development misses. '
                 'The remaining pool is therefore selected by previous review history, not representative of all500 queries. '
                 'The original accepted labels remain unchanged. This report uses separate, newly assessed grades for both conditions.</p></html>')
    (OUTPUT/'review.html').write_text('\n'.join(parts),encoding='utf-8')
    print(OUTPUT/'review.html')


if __name__=='__main__':main()
