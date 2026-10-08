"""Run every module's main.py against real QEMU, the way CI does.

Each module launches its own QEMU (launching QEMU is what this course is
about), waits for it properly, checks its claims with assert, and cleans up.
A module passes when its main.py exits 0.

Usage: python verify.py            # all modules
       python verify.py 03 07      # just these
Needs qemu-system-i386 and qemu-img (PATH, or C:\\Program Files\\qemu), and
`pip install -r requirements.txt` for Module 07's MCP server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
KERNEL = os.path.join(ROOT, "kernel", "kernel.elf")


def main() -> int:
    wanted = sys.argv[1:]
    modules = sorted(
        d for d in os.listdir(ROOT)
        if os.path.isfile(os.path.join(ROOT, d, "main.py"))
        and (not wanted or any(d.startswith(w) for w in wanted))
    )
    if not modules:
        print("no modules matched")
        return 1
    failed = []
    for m in modules:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "main.py", "--kernel", KERNEL],
                cwd=os.path.join(ROOT, m), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300,
            )
            ok, output = proc.returncode == 0, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            ok, output = False, "timed out after 300s"
        print(f"{'PASS' if ok else 'FAIL'}  {m}  ({time.monotonic() - start:.1f}s)")
        if not ok:
            failed.append(m)
            print("    " + output.strip().replace("\n", "\n    "))
    print(f"\n{len(modules) - len(failed)}/{len(modules)} modules verified against real QEMU")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
