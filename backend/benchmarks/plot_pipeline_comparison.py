"""Render a completed pipeline analysis; requires optional matplotlib."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    report=json.loads(args.analysis.read_text());cells=report['cells']
    if not cells or any(c['attempts_per_mode']<100 for c in cells):
        raise ValueError('This primary chart requires at least 100 observations per mode/cell')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,
        'axes.spines.right':False,'axes.spines.left':False,'axes.edgecolor':'#CCD5DF','svg.fonttype':'none',
        'svg.hashsalt':'vindex-pipeline-comparison'})
    fig,axes=plt.subplots(2,len(cells),figsize=(4.5*len(cells),7.6),squeeze=False)
    fig.patch.set_facecolor('#FFFFFF')
    for col,cell in enumerate(cells):
        seconds='80s' if '80s' in cell['fixture'] else '20s'
        title=f"{cell['worker_state'].capitalize()} workers · {seconds} video\n{cell['concurrency']} simultaneous upload"+('s' if cell['concurrency']!=1 else '')
        for row,(metric,label) in enumerate([('searchable','Searchable'),('playable','Playable')]):
            ax=axes[row,col];values=cell['metrics'][metric]['p95']
            points=np.array([values[m+'_seconds'] for m in ['api','direct']])
            intervals=np.array([values[m+'_seconds_95pct_wave_bootstrap'] for m in ['api','direct']])
            ax.bar([0,1],points,color=['#37516B','#087F82'],width=.58,zorder=3)
            ax.errorbar([0,1],points,yerr=np.maximum(0,np.array([points-intervals[:,0],intervals[:,1]-points])),
                fmt='none',ecolor='#162C43',capsize=5,linewidth=1.3,zorder=4)
            top=max(intervals[:,1])*1.27;ax.set_ylim(0,top)
            for i,point in enumerate(points):
                ax.text(i,intervals[i,1]+top*.035,f'{point:.1f}s',ha='center',weight='bold',color='#162C43')
            ax.set_xticks([0,1],['Through API','Direct to S3']);ax.tick_params(axis='both',length=0)
            ax.yaxis.grid(True,color='#E9EDF2',zorder=0);ax.set_ylabel(label+' p95 (seconds)' if col==0 else 'Seconds')
            if row==0: ax.set_title(title,pad=17,fontsize=12,weight='bold',color='#162C43')
            lo,hi=values['ratio_95pct_paired_wave_bootstrap']
            gate=cell['gates'][metric+'_p95_non_regression']
            ax.text(.5,-.21,f"Direct/API: {values['direct_over_api']:.3f}  ·  95% CI {lo:.3f}–{hi:.3f}\n10% non-regression gate: {gate}",
                ha='center',va='top',transform=ax.transAxes,fontsize=9,color='#33465A')
    fig.suptitle('Time from upload click to a usable video',x=.04,y=.99,ha='left',fontsize=20,weight='bold',color='#162C43')
    measured_uploads=sum(2*cell['attempts_per_mode'] for cell in cells)
    fig.text(.04,.935,f'{measured_uploads} measured uploads · Same processing DAG · Observed readiness includes polling and playback startup',fontsize=11,color='#526579')
    fig.text(.04,.015,'Bars: p95. Whiskers: 95% wave-bootstrap intervals. Ratios use paired waves; concurrent clients stay together.\nSynthetic speech on one isolated host; household networks, production SLOs and reliability are not established.',fontsize=9,color='#526579')
    fig.subplots_adjust(left=.07,right=.98,top=.84,bottom=.15,wspace=.3,hspace=.63)
    args.output.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.output/'comparison.png',dpi=160,facecolor='white')
    svg_path=args.output/'comparison.svg'
    fig.savefig(svg_path,facecolor='white',metadata={'Date':None})
    svg_path.write_text('\n'.join(line.rstrip() for line in svg_path.read_text(encoding='utf-8').splitlines())+'\n',
                        encoding='utf-8',newline='\n')
    plt.close(fig)


if __name__=='__main__': main()
