"""Module 06: snapshots - savevm/loadvm through HMP, and qcow2 detection.

Run:  python main.py --kernel ../kernel/kernel.elf
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any

QCOW2_MAGIC = b"QFI\xfb"


def disk_format(path: str) -> str:
    """qcow2 or raw, from the file's first four bytes.

    qemu-mcp once hard-coded `format=raw` on every disk. A qcow2 file opened
    as raw boots fine (the guest just sees qcow2 metadata as disk bytes),
    but every snapshot command fails - and nothing says why until you look.
    """
    with open(path, "rb") as fh:
        return "qcow2" if fh.read(4) == QCOW2_MAGIC else "raw"


TAG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def hmp(qmp: "QMP", command_line: str) -> str:
    """Run an HMP command over QMP.

    qemu-mcp uses HMP's one-shot savevm/loadvm (QMP's snapshot-save is a
    background job). The catch: `human-monitor-command` always SUCCEEDS at the
    QMP level. A failed savevm comes back as a normal return value holding
    the error text. Treat any output from savevm/loadvm/delvm as a failure.
    """
    out: str = qmp.command("human-monitor-command", **{"command-line": command_line})
    return out


def snapshot(qmp: "QMP", verb: str, tag: str) -> None:
    # The tag is spliced into a command line. Restrict it instead of trying
    # to quote it: "s1 extra" or "s1\nquit" must never reach the monitor.
    if not TAG_RE.match(tag):
        raise ValueError(f"invalid snapshot tag {tag!r} - use letters, digits, '.', '_', '-'")
    out = hmp(qmp, f"{verb} {tag}").strip()
    if out:
        raise RuntimeError(f"{verb} {tag} failed: {out}")


# ---- QMP + process helpers (Modules 01-05, condensed) -------------------
class QMP:
    def __init__(self, port: int):
        deadline = time.monotonic() + 20
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        self.sock.settimeout(60)        # savevm of a small VM takes ~1s; be generous
        self.f = self.sock.makefile("rwb")
        self.f.readline()
        self.command("qmp_capabilities")

    def command(self, name: str, **arguments: Any) -> Any:
        msg: dict[str, Any] = {"execute": name}
        if arguments:
            msg["arguments"] = arguments
        self.f.write(json.dumps(msg).encode() + b"\n")
        self.f.flush()
        while True:
            resp = json.loads(self.f.readline())
            if "event" in resp:
                continue
            if "error" in resp:
                raise RuntimeError(f"{name}: {resp['error']['desc']}")
            return resp["return"]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def find_tool(name: str) -> str:
    hit = shutil.which(name)
    if hit:
        return hit
    win = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "qemu", name + ".exe")
    if os.path.isfile(win):
        return win
    raise FileNotFoundError(f"{name} not found")


def normalize(data: bytes) -> str:
    return data.replace(b"\r", b"").decode("utf-8", errors="replace")


def wait_serial(log: str, marker: str, since: int = 0, timeout: float = 30) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(log, "rb") as fh:
                fh.seek(since)
                new = fh.read()
        except FileNotFoundError:
            new = b""
        if marker in normalize(new):
            return since + len(new)
        time.sleep(0.05)
    raise TimeoutError(f"{marker!r} not on serial within {timeout}s")


def boot(kernel: str, disk: str, fmt: str, workdir: str) -> tuple[subprocess.Popen[bytes], QMP, socket.socket, str]:
    qport, sport = free_port(), free_port()
    log = os.path.join(workdir, f"serial-{fmt}.log").replace("\\", "/")
    proc = subprocess.Popen(
        [find_tool("qemu-system-i386"), "-display", "none", "-kernel", kernel, "-m", "64",
         "-drive", f"file={disk},format={fmt},if=ide",
         "-chardev", f"socket,id=s0,host=127.0.0.1,port={sport},server=on,wait=off,"
                     f"logfile={log},logappend=on",
         "-serial", "chardev:s0",
         "-qmp", f"tcp:127.0.0.1:{qport},server=on,wait=off"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    qmp = QMP(qport)
    serial = socket.create_connection(("127.0.0.1", sport), timeout=10)
    return proc, qmp, serial, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    args = ap.parse_args()
    kernel = os.path.abspath(args.kernel)

    workdir = tempfile.mkdtemp(prefix="qemu-hard-way-06-")
    disk = os.path.join(workdir, "disk.qcow2").replace("\\", "/")
    subprocess.run([find_tool("qemu-img"), "create", "-q", "-f", "qcow2", disk, "16M"], check=True)
    print(f"created {os.path.basename(disk)}: first bytes {open(disk, 'rb').read(4)!r} -> "
          f"detected {disk_format(disk)}")
    assert disk_format(disk) == "qcow2"

    for bad in ("s1 extra", "s1\nquit", ""):
        try:
            snapshot(None, "savevm", bad)  # type: ignore[arg-type]
            raise AssertionError(f"tag {bad!r} should be rejected")
        except ValueError:
            pass

    procs = []
    try:
        # 1) The old bug: a qcow2 file opened as raw. Boots fine; savevm fails,
        #    and the failure arrives as a RETURN string, not a QMP error.
        proc, qmp, serial, log = boot(kernel, disk, "raw", workdir)
        procs.append(proc)
        wait_serial(log, "ready")
        out = hmp(qmp, "savevm s1").strip()
        print(f"format=raw on a qcow2 file -> savevm returned: {out!r}")
        assert out, "savevm on a raw-opened disk should report an error"
        try:
            snapshot(qmp, "savevm", "s1")
            raise AssertionError("snapshot() must turn that text into an exception")
        except RuntimeError as e:
            print(f"snapshot() raises: {str(e)[:80]}...")
        proc.kill()
        proc.wait()
        serial.close()

        # 2) Detected format: snapshots work, and loadvm rewinds guest state.
        proc, qmp, serial, log = boot(kernel, disk, disk_format(disk), workdir)
        procs.append(proc)
        offset = wait_serial(log, "ready")
        serial.sendall(b"count ")           # half a command, no Enter yet
        time.sleep(0.3)
        snapshot(qmp, "savevm", "half-typed")
        print("saved snapshot 'half-typed' with 'count ' in the guest's line buffer")

        serial.sendall(b"7\r")
        offset = wait_serial(log, "count=7", since=offset)
        print("finished the line with '7' -> count=7")

        snapshot(qmp, "loadvm", "half-typed")
        serial.sendall(b"8\r")
        wait_serial(log, "count=8", since=offset)
        print("loadvm, then typed '8' -> count=8: the guest's half-typed 'count ' came back")

        listing = hmp(qmp, "info snapshots")
        print("info snapshots:\n  " + "\n  ".join(listing.strip().splitlines()))
        assert "half-typed" in listing

        try:
            snapshot(qmp, "loadvm", "nope")
            raise AssertionError("loading a missing snapshot should fail")
        except RuntimeError as e:
            print(f"missing tag -> {e}")
        serial.close()
    finally:
        for p in procs:
            p.kill()
            p.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
