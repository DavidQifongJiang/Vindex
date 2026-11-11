"""Render the completed comparison; no live services or data transformations."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('summary', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    value = json.loads(args.summary.read_text())
    colors = {'api': '#C66B24', 'direct': '#087E8B'}
    labels = {'api': 'Upload through API', 'direct': 'Direct upload to S3'}
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'axes.spines.top': False,
                         'axes.spines.right': False, 'axes.titleweight': 'bold'})
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.8), dpi=180)
    for mode in ['api', 'direct']:
        rows = sorted([r for r in value['cohorts'] if r['mode'] == mode], key=lambda r: r['rate'])
        rates = [r['rate'] for r in rows]
        means = [r['search_p95_mean_seconds']*1000 for r in rows]
        lower = [m-r['search_p95_range_seconds'][0]*1000 for m, r in zip(means, rows)]
        upper = [r['search_p95_range_seconds'][1]*1000-m for m, r in zip(means, rows)]
        axes[0].errorbar(rates, means, yerr=[lower, upper], color=colors[mode], marker='o', markersize=6,
                         linewidth=2.2, capsize=4, label=labels[mode])
        axes[1].plot(rates, [r['api_allocated_cpu_mean']*100 for r in rows], color=colors[mode], marker='o',
                     markersize=6, linewidth=2.2, label=labels[mode])
    axes[0].set(title='Search responsiveness during uploads', ylabel='Search p95 (milliseconds)', ylim=(0, None))
    axes[1].set(title='Use of the fixed API CPU allocation', ylabel='Allocated API CPU used (%)', ylim=(0, 105))
    for ax in axes:
        ax.set_xlabel('Offered video uploads per minute')
        ax.set_xticks(sorted({r['rate'] for r in value['cohorts']}))
        ax.grid(axis='y', alpha=.2)
        ax.legend(frameon=False, loc='upper left')
    fig.suptitle('VIndex: existing-video search under competing uploads', fontsize=16, fontweight='bold', y=.99)
    fig.text(.5, .89, 'Same API allocation: 0.5 vCPU / 2 GiB  ·  25.8 MB videos  ·  2 semantic searches/second', ha='center', fontsize=10.5, color='#475569')
    fig.text(.5, .02, 'Three paired repetitions per rate. Points show trial means; search whiskers show the three-trial range.\nNew-video processing paused; these measurements describe admission/search interference.',
             ha='center', fontsize=9, color='#475569')
    fig.tight_layout(rect=(0, .08, 1, .87))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, facecolor='white')
    fig.savefig(args.output.with_suffix('.svg'), facecolor='white')


if __name__ == '__main__': main()
