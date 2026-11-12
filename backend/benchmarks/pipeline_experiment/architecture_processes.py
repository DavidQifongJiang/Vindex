"""Identify the live prefork pool without relying only on container restarts."""
from pathlib import Path


def live_children(parent_pid=1, proc_root=Path('/proc')):
    children = (proc_root/str(parent_pid)/'task'/str(parent_pid)/'children').read_text().split()
    result = []
    for pid in children:
        directory = proc_root/pid
        status = directory.joinpath('status').read_text().splitlines()
        namespace_pid = int(next(line for line in status if line.startswith('NSpid:')).split()[-1])
        # /proc/PID/stat field 22; comm may contain spaces or parentheses.
        fields = directory.joinpath('stat').read_text().rsplit(')', 1)[1].split()
        result.append({'namespace_pid': namespace_pid, 'start_ticks': int(fields[19])})
    return sorted(result, key=lambda r:r['namespace_pid'])


def same_pool(expected, observed):
    return len(expected) == len(observed) == 4 and sorted(expected, key=lambda r:r['namespace_pid']) == sorted(observed, key=lambda r:r['namespace_pid'])
