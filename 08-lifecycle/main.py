"""Module 08: lifecycle - named VMs, stopping them, VMs that stop themselves,
boots that fail, and never leaking a temp directory.

Run:  python main.py --kernel ../kernel/kernel.elf
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any


class QMPError(RuntimeError):
    pass


class QMP:
    def __init__(self, port: int, proc: subprocess.Popen[bytes], timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while True:
            if proc.poll() is not None:
                raise QMPError(f"QEMU exited immediately (code {proc.returncode})")
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise QMPError(f"QMP never came up on port {port}") from None
                time.sleep(0.1)
        self.sock.settimeout(10)
        self.f = self.sock.makefile("rwb")
        self.events: list[str] = []
        self._read()
        self.command("qmp_capabilities")

    def _read(self) -> dict[str, Any]:
        try:
            line = self.f.readline()
        except (ConnectionResetError, ConnectionAbortedError):
            line = b""
        if not line:
            raise QMPError("QMP connection closed by QEMU")
        msg: dict[str, Any] = json.loads(line)
        return msg

    def command(self, name: str, **arguments: Any) -> Any:
        msg: dict[str, Any] = {"execute": name}
        if arguments:
            msg["arguments"] = arguments
        self.f.write(json.dumps(msg).encode() + b"\n")
        self.f.flush()
        while True:
            resp = self._read()
            if "event" in resp:
                self.events.append(resp["event"])
                continue
            if "error" in resp:
                raise QMPError(f"{name}: {resp['error']['desc']}")
            return resp["return"]

    def drain_events(self) -> None:
        """Read whatever QEMU sent before closing (e.g. SHUTDOWN)."""
        try:
            while True:
                resp = self._read()
                if "event" in resp:
                    self.events.append(resp["event"])
        except (QMPError, OSError, ValueError):
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def find_qemu() -> str:
    hit = shutil.which("qemu-system-i386")
    if hit:
        return hit
    win = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "qemu",
                       "qemu-system-i386.exe")
    if os.path.isfile(win):
        return win
    raise FileNotFoundError("qemu-system-i386 not found")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


@dataclass
class VM:
    name: str
    proc: subprocess.Popen[bytes]
    qmp: QMP
    workdir: str
    started: float = field(default_factory=time.monotonic)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None


VMS: dict[str, VM] = {}


def validate_name(name: str) -> None:
    # Checked FIRST, before any filesystem work. A name like "a/b" used to
    # reach tempfile.mkdtemp(prefix=...) and fail with a baffling
    # FileNotFoundError about a path nobody asked for.
    if not name or any(c in name for c in "/\\") or name in (".", ".."):
        raise ValueError(f"invalid VM name {name!r}: must be non-empty with no path separators")


def boot(name: str, kernel: str, extra: list[str] | None = None) -> VM:
    validate_name(name)
    if name in VMS:
        raise RuntimeError(f"a VM named {name!r} is already running - stop it first")
    workdir = tempfile.mkdtemp(prefix=f"qemu-hard-way-08-{name}-")
    port = free_port()
    stderr_path = os.path.join(workdir, "qemu.stderr")
    with open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            [find_qemu(), "-display", "none", "-kernel", kernel, "-m", "64",
             "-serial", "file:" + os.path.join(workdir, "serial.log"),
             "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off", *(extra or [])],
            stdout=subprocess.DEVNULL, stderr=err)
    try:
        qmp = QMP(port, proc)
    except QMPError as e:
        # Every failure path cleans up: reap the process (so its files are
        # closed - Windows can't delete open files) and remove the workdir.
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        with open(stderr_path, errors="replace") as fh:
            why = fh.read().strip()
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(f"boot of {name!r} failed: {e}. QEMU said: {why[:200]}") from None
    vm = VM(name, proc, qmp, workdir)
    VMS[name] = vm
    return vm


def stop(name: str, graceful_timeout: float = 10.0) -> str:
    vm = VMS.pop(name)
    how = "already exited"
    if vm.running:
        try:
            # QEMU may close the socket before replying to quit (8.2 does,
            # 11.0 replies first). The process exiting is what matters.
            vm.qmp.command("quit")
        except QMPError:
            pass
        try:
            vm.proc.wait(timeout=graceful_timeout)
            how = "quit"
        except subprocess.TimeoutExpired:
            vm.proc.kill()
            vm.proc.wait()
            how = "killed after quit timed out"
    vm.qmp.close()
    shutil.rmtree(vm.workdir, ignore_errors=True)
    return how


def reap_dead() -> list[str]:
    """Forget VMs whose QEMU exited on its own (guest poweroff, crash)."""
    dead = [n for n, vm in VMS.items() if not vm.running]
    for n in dead:
        vm = VMS.pop(n)
        vm.qmp.drain_events()
        vm.qmp.close()
        shutil.rmtree(vm.workdir, ignore_errors=True)
    return dead


def wait_serial(vm: VM, marker: bytes, timeout: float = 30) -> None:
    log = os.path.join(vm.workdir, "serial.log")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(log, "rb") as fh:
                if marker in fh.read():
                    return
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"{marker!r} not seen on {vm.name}")


def type_text(vm: VM, text: str) -> None:
    keys = {"\n": "ret", " ": "spc"}
    for ch in text:
        vm.qmp.command("send-key", keys=[{"type": "qcode", "data": keys.get(ch, ch)}])
        time.sleep(0.02)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    args = ap.parse_args()
    kernel = os.path.abspath(args.kernel)
    workdirs: list[str] = []
    try:
        a, b = boot("alpha", kernel), boot("beta", kernel)
        workdirs += [a.workdir, b.workdir]
        wait_serial(a, b"ready")
        wait_serial(b, b"ready")
        print(f"two named VMs running: {sorted(VMS)} (pids {a.proc.pid}, {b.proc.pid})")

        for bad, expect in (("alpha", "already running"), ("a/b", "invalid VM name"),
                            ("..", "invalid VM name")):
            try:
                boot(bad, kernel)
                raise AssertionError(f"boot({bad!r}) should fail")
            except (RuntimeError, ValueError) as e:
                assert expect in str(e), e
        print("duplicate and path-like names rejected before touching the filesystem")

        how = stop("alpha")
        print(f"stop('alpha') -> {how}; process exit code {a.proc.returncode}; "
              f"workdir removed: {not os.path.exists(a.workdir)}")
        assert how == "quit" and not os.path.exists(a.workdir)

        # beta powers itself off from inside: QEMU exits, and the VM must be
        # noticed and cleaned up even though nobody called stop().
        type_text(b, "poweroff\n")
        b.proc.wait(timeout=30)
        dead = reap_dead()
        # The exit code is the dependable signal. The SHUTDOWN event QEMU
        # emits on its way out may never be readable: here, on Windows, the
        # drained socket gave nothing. Run with -no-shutdown if you need it.
        print(f"guest typed poweroff -> QEMU exited with {b.proc.returncode}; "
              f"reaped {dead}; events readable after exit: {b.qmp.events or 'none'}")
        assert dead == ["beta"] and b.proc.returncode == 0
        assert not os.path.exists(b.workdir) and not VMS

        # With -no-shutdown, QEMU pauses instead of exiting, and the event
        # arrives on a socket that is still open.
        c = boot("gamma", kernel, extra=["-no-shutdown"])
        workdirs.append(c.workdir)
        wait_serial(c, b"ready")
        type_text(c, "poweroff\n")
        deadline = time.monotonic() + 30
        while "SHUTDOWN" not in c.qmp.events and time.monotonic() < deadline:
            c.qmp.command("query-status")
            time.sleep(0.1)
        status = c.qmp.command("query-status")
        print(f"with -no-shutdown: events {c.qmp.events}, status {status['status']!r}, "
              f"process still alive: {c.running}")
        assert "SHUTDOWN" in c.qmp.events and c.running
        assert stop("gamma") == "quit"

        # A boot that can't work: QEMU exits at once. The error carries
        # QEMU's own reason, and no temp directory survives.
        before = set(os.listdir(tempfile.gettempdir()))
        try:
            boot("broken", os.path.join(os.path.dirname(kernel), "missing.elf"))
            raise AssertionError("boot of a missing kernel should fail")
        except RuntimeError as e:
            print(f"failed boot -> {str(e)[:150]}")
            assert "QEMU said:" in str(e)
        leaked = [d for d in set(os.listdir(tempfile.gettempdir())) - before
                  if d.startswith("qemu-hard-way-08-broken")]
        assert not leaked, f"leaked workdirs: {leaked}"
        print("no workdir leaked by the failed boot")
    finally:
        for name in list(VMS):
            stop(name)
        for d in workdirs:
            shutil.rmtree(d, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
