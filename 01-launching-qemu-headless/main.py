"""Module 01: find the QEMU binary, launch it headless, and know when it booted.

Run:  python main.py --kernel ../kernel/kernel.elf
Every claim in this module's README is checked by an assert below.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time


def windows_search_dirs() -> list[str]:
    """Where the official Windows installer puts QEMU, plus $QEMU_DIR.

    The installer doesn't touch PATH, so `shutil.which` alone finds nothing on
    a fresh Windows machine. Some installs use a versioned subdirectory
    (C:\\Program Files\\qemu\\qemu-8.2.0), so one level of subdirectories is
    searched too.
    """
    dirs = []
    if os.environ.get("QEMU_DIR"):
        dirs.append(os.environ["QEMU_DIR"])
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        base = os.path.join(root, "qemu")
        dirs.append(base)
        try:
            for entry in os.listdir(base):
                sub = os.path.join(base, entry)
                if os.path.isdir(sub):
                    dirs.append(sub)
        except OSError:
            continue
    return dirs


def installed_arches() -> list[str]:
    """Every qemu-system-* binary we can see, for a useful error message."""
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    if sys.platform == "win32":
        dirs = windows_search_dirs() + dirs
    found = set()
    for d in dirs:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for e in entries:
            name = e[:-4] if e.lower().endswith(".exe") else e
            if name.startswith("qemu-system-") and not name.endswith("w"):
                found.add(name[len("qemu-system-"):])
    return sorted(found)


def find_qemu(arch: str) -> str:
    if not arch or any(c in arch for c in "/\\") or arch in (".", ".."):
        raise ValueError(f"invalid arch {arch!r}: it becomes part of a file name")
    exe = f"qemu-system-{arch}"
    hit = shutil.which(exe)
    if hit:
        return hit
    if sys.platform == "win32":
        for d in windows_search_dirs():
            cand = os.path.join(d, exe + ".exe")
            if os.path.isfile(cand):
                return cand
    found = installed_arches()
    hint = f" Found on this system: {', '.join(found)}." if found else " No QEMU found at all."
    raise FileNotFoundError(f"{exe} not found. Install QEMU and put it on PATH "
                            f"(or set QEMU_DIR on Windows).{hint}")


def boot(qemu: str, kernel: str, workdir: str) -> tuple[subprocess.Popen[bytes], str]:
    """Start QEMU with no window and the guest's COM1 going to a file."""
    serial_log = os.path.join(workdir, "serial.log")
    cmd = [
        qemu,
        "-display", "none",        # headless: no SDL/GTK window
        "-kernel", kernel,         # multiboot kernel, no disk needed
        "-m", "64",
        "-serial", f"file:{serial_log}",
        "-no-reboot",              # a triple fault exits instead of looping
    ]
    # QEMU's own complaints (bad flag, missing file) go to stderr: keep them.
    err = open(os.path.join(workdir, "qemu.stderr"), "wb")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
    err.close()
    return proc, serial_log


def wait_serial(proc: subprocess.Popen[bytes], log: str, marker: bytes,
                timeout: float) -> float:
    """Poll the serial log until `marker` appears. Returns seconds waited."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited with code {proc.returncode} before {marker!r}")
        try:
            with open(log, "rb") as fh:
                if marker in fh.read():
                    return time.monotonic() - start
        except FileNotFoundError:
            pass  # QEMU creates the file a moment after starting
        time.sleep(0.05)
    raise TimeoutError(f"{marker!r} did not appear on serial within {timeout}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--arch", default="i386")
    args = ap.parse_args()

    qemu = find_qemu(args.arch)
    print(f"found {qemu}")
    version = subprocess.run([qemu, "--version"], capture_output=True, text=True,
                             timeout=30).stdout.splitlines()[0]
    print(version)
    assert version.startswith("QEMU emulator version"), version

    # A wrong arch must name what IS installed, not just say "not found".
    try:
        find_qemu("nosucharch")
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError as e:
        print(f"wrong arch -> {str(e)[:110]}...")
        assert "Found on this system:" in str(e) and "i386" in str(e)

    workdir = tempfile.mkdtemp(prefix="qemu-hard-way-01-")
    proc, log = boot(qemu, os.path.abspath(args.kernel), workdir)
    try:
        waited = wait_serial(proc, log, b"ready", timeout=60)
        print(f"guest printed 'ready' after {waited:.1f}s")
        with open(log, "rb") as fh:
            text = fh.read()
        print(f"serial log so far: {text!r}")
        assert b"kernel: hello from COM1" in text
        assert proc.poll() is None, "QEMU should still be running"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    assert not os.path.exists(workdir)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
