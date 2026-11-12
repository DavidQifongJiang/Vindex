"""Plot every primary repetition with readiness seconds and search milliseconds."""
import argparse
import json
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.summary.read_text())
    assert not report['partial'] and report['progress']['finished']
    stages = 'abcd'
    labels = ['A\nSerialized', 'B\nIndependent\nbranches', 'C\nFive-minute\nchunks', 'D\nDirect S3\nuploads']
    colors = ['#64748b', '#0284c7', '#7c3aed', '#059669']
    rates = sorted({row['rate'] for row in report['trials']})
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.2))
    fig.suptitle('VIndex: two user-visible improvements', fontsize=20, weight='bold', x=.07, ha='left')
    for ax, metric, rate, title, ylabel in [
        (axes[0], 'readiness', rates[0], 'New video becomes searchable', 'Time after upload acceptance (seconds)'),
        (axes[1], 'search', rates[-1], 'Existing videos remain responsive', 'Mean trial search p95 (milliseconds)')]:
        labels_to_place = []
        maxima = []
        for index, stage in enumerate(stages):
            rows = sorted([r for r in report['trials'] if r['stage'] == stage and r['rate'] == rate], key=lambda r:r['repeat'])
            valid = len(rows) == 3 and all(r['arrival_validation_pass'] and r['integrity_pass'] and r['upload_success_pass']
                and r['phases']['load']['success_fraction'] >= .99 for r in rows)
            values = ([r['readiness']['after_acceptance_seconds'] for r in rows if r['readiness']['outcome'] == 'ready']
                      if metric == 'readiness' else [r['phases']['load']['p95_seconds'] * 1000 for r in rows])
            complete = valid and len(values) == 3
            if complete:
                average = mean(values)
                ax.bar(index, average, color=colors[index], alpha=.88, width=.63, zorder=2)
                labels_to_place.append((index, max(values), f'{average:.1f}' if metric == 'readiness' else f'{average:.0f}'))
            else:
                ax.text(index, .02, f'{len(values)}/3 observed\n' + ('invalid workload' if not valid else 'censored'),
                        transform=ax.get_xaxis_transform(), ha='center', fontsize=9, color='#991b1b')
            if values:
                maxima.append(max(values))
                offsets = [-.12, 0, .12][:len(values)]
                ax.scatter([index + offset for offset in offsets], values, s=35, c='#0f172a',
                           edgecolors='white', linewidths=.8, zorder=3)
        ax.set_title(title + f'\n{rate} background uploads/min + 2 searches/sec', fontsize=13, loc='left', pad=15)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_xticks(range(4), labels, fontsize=10)
        ceiling = max(maxima, default=1)
        ax.set_ylim(0, ceiling * 1.20)
        for index, top, label in labels_to_place:
            ax.text(index, top + ceiling * .04, label,
                    ha='center', fontsize=12, weight='bold', color='#0f172a')
        ax.grid(axis='y', color='#e2e8f0', zorder=0)
        ax.set_axisbelow(True)
        for spine in ['top', 'right']: ax.spines[spine].set_visible(False)
        ax.spines['left'].set_color('#cbd5e1'); ax.spines['bottom'].set_color('#cbd5e1')
    fig.text(.07, .075, 'Same capacity in every stage: API 0.5 vCPU / 2 GiB; processing 4 vCPU / 10 GiB.', fontsize=10, color='#334155')
    fig.text(.07, .04, 'Bars: mean of three trials. Dots: every trial. Readiness uses a 15-minute video; background uploads are 25.8 MB.', fontsize=10, color='#334155')
    fig.subplots_adjust(left=.075, right=.975, top=.78, bottom=.24, wspace=.24)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ['.png', '.svg']: fig.savefig(args.output.with_suffix(suffix), dpi=180, facecolor='white')
    plt.close(fig)


if __name__ == '__main__': main()
