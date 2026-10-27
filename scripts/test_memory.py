"""Runs pytest and reports the peak memory of it and every process it starts; stops it past a limit. Linux only.

The Layout-kept tests run BabelDOC (its layout model, its fonts, its subprocesses), and a run of them once filled a
14 GB laptop. This measures a run without risking that: `ALDI_TEST_MEMORY_LIMIT_MB` (default 4096) is where it stops.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

LIMIT_MB = int(os.environ.get("ALDI_TEST_MEMORY_LIMIT_MB", "4096"))


def tree_rss(root: int) -> dict[int, tuple[int, str]]:
    """Each process in `root`'s tree: its resident memory in MB and its command line."""
    children: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            fields = dict(line.split(":", 1) for line in status.read_text().splitlines() if ":" in line)
        except OSError:
            continue
        pid = int(status.parent.name)
        children.setdefault(int(fields["PPid"]), []).append(pid)
        rss[pid] = int(fields.get("VmRSS", "0 kB").split()[0]) // 1024
    tree, stack = {}, [root]
    while stack:
        pid = stack.pop()
        try:
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode()[:100]
        except OSError:
            command = "?"
        tree[pid] = (rss.get(pid, 0), command)
        stack.extend(children.get(pid, []))
    return tree


def main() -> int:
    if not sys.platform.startswith("linux"):
        sys.exit("test-memory reads /proc: Linux only.")
    # Its own process group, so stopping it also stops the subprocesses BabelDOC starts.
    run = subprocess.Popen([sys.executable, "-m", "pytest", *sys.argv[1:]], start_new_session=True)
    peak, at_peak, stopped = 0, {}, False
    while run.poll() is None:
        tree = tree_rss(run.pid)
        if (total := sum(mb for mb, _command in tree.values())) > peak:
            peak, at_peak = total, tree
        if peak > LIMIT_MB and not stopped:
            print(f"\ntest-memory: over {LIMIT_MB} MB, stopping the run", flush=True)
            os.killpg(run.pid, signal.SIGKILL)
            stopped = True
        time.sleep(0.2)
    print(f"test-memory: peak {peak} MB{' (stopped at the limit)' if stopped else ''}", flush=True)
    for pid, (mb, command) in sorted(at_peak.items(), key=lambda item: -item[1][0]):
        print(f"  {mb:>6} MB  {pid}  {command}")
    return 1 if stopped else run.returncode


if __name__ == "__main__":
    sys.exit(main())
