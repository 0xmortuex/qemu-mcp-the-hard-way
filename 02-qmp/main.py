"""Module 02: a QMP client from scratch - greeting, capabilities, commands,
async events, the lock, and timeouts.

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
import threading
import time
from typing import Any


class QMPError(RuntimeError):
    pass


class QMP:
    """QMP is newline-delimited JSON over a socket.

    1. QEMU speaks first: a greeting {"QMP": {...}}.
    2. Nothing works until we send qmp_capabilities.
    3. Each command gets exactly one {"return": ...} or {"error": ...}.
    4. Async {"event": ...} messages can arrive at ANY time, including in
       between a command and its reply - so the reply loop must skip them.
    """

    def __init__(self, port: int, connect_timeout: float = 20.0, read_timeout: float = 15.0):
        deadline = time.monotonic() + connect_timeout
        last: OSError | None = None
        self.sock: socket.socket | None = None
        # QEMU opens the port a moment after it starts (much longer on a cold
        # machine with antivirus scanning the binary): retry, don't one-shot.
        while time.monotonic() < deadline:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError as e:
                last = e
                time.sleep(0.1)
        if self.sock is None:
            raise QMPError(f"could not connect to QMP on 127.0.0.1:{port}: {last}")
        self.sock.settimeout(read_timeout)
        self.buf = b""
        self.events: list[dict[str, Any]] = []
        # QMP has no request ids. Two threads sharing the socket without a
        # lock can each read the OTHER's reply. Hold it for send+receive.
        self.lock = threading.Lock()
        self.greeting = self._read()
        self.command("qmp_capabilities")

    def _read(self) -> dict[str, Any]:
        assert self.sock is not None
        while b"\n" not in self.buf:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                raise QMPError("QMP read timed out - QEMU stopped responding") from None
            except (ConnectionResetError, ConnectionAbortedError) as e:
                raise QMPError(f"QMP connection closed by QEMU ({e})") from None
            if not chunk:
                raise QMPError("QMP connection closed by QEMU")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        msg: dict[str, Any] = json.loads(line)
        return msg

    def command(self, name: str, **arguments: Any) -> Any:
        assert self.sock is not None
        with self.lock:
            msg: dict[str, Any] = {"execute": name}
            if arguments:
                msg["arguments"] = arguments
            self.sock.sendall(json.dumps(msg).encode() + b"\n")
            while True:
                resp = self._read()
                if "event" in resp:
                    self.events.append(resp)   # keep it; it's not our reply
                    continue
                if "error" in resp:
                    raise QMPError(f"{name}: {resp['error'].get('desc', resp['error'])}")
                return resp["return"]

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def find_qemu(arch: str) -> str:
    """Module 01's lookup, condensed."""
    exe = f"qemu-system-{arch}"
    hit = shutil.which(exe)
    if hit:
        return hit
    win = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "qemu", exe + ".exe")
    if os.path.isfile(win):
        return win
    raise FileNotFoundError(f"{exe} not found")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    args = ap.parse_args()

    qmp_port = free_port()
    workdir = tempfile.mkdtemp(prefix="qemu-hard-way-02-")
    proc = subprocess.Popen(
        [find_qemu("i386"), "-display", "none", "-kernel", os.path.abspath(args.kernel),
         "-m", "64", "-serial", "file:" + os.path.join(workdir, "serial.log"),
         # server=on: QEMU listens. wait=off: boot without waiting for us.
         "-qmp", f"tcp:127.0.0.1:{qmp_port},server=on,wait=off"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        qmp = QMP(qmp_port)
        ver = qmp.greeting["QMP"]["version"]["qemu"]
        print(f"greeting: QEMU {ver['major']}.{ver['minor']}.{ver['micro']}, "
              f"capabilities {qmp.greeting['QMP']['capabilities']}")
        assert "QMP" in qmp.greeting

        status = qmp.command("query-status")
        print(f"query-status -> {status}")
        assert status["running"] is True

        # Errors come back as {"error": {"class", "desc"}} - not exceptions
        # on the wire, so the client must turn them into one.
        try:
            qmp.command("no-such-command")
            raise AssertionError("expected QMPError")
        except QMPError as e:
            print(f"bad command -> {e}")
            assert "no-such-command" in str(e)

        # stop/cont each emit an async event (STOP, RESUME). They arrive on
        # the same socket, interleaved with replies; command() files them.
        qmp.command("stop")
        assert qmp.command("query-status")["running"] is False
        qmp.command("cont")
        qmp.command("query-status")          # gives the RESUME event time to land
        names = [e["event"] for e in qmp.events]
        print(f"events seen while running commands: {names}")
        assert "STOP" in names and "RESUME" in names

        # The lock: 8 threads x 25 commands on one socket. Without the lock,
        # replies get swapped between threads and the checks below fail.
        errors: list[str] = []

        def hammer(n: int) -> None:
            for _ in range(25):
                if n % 2:
                    r = qmp.command("query-status")
                    if "running" not in r:
                        errors.append(f"thread {n} got {r}")
                else:
                    r = qmp.command("query-name")
                    if "running" in r:
                        errors.append(f"thread {n} got {r}")

        threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        print(f"200 concurrent commands, mismatched replies: {len(errors)}")
        assert not errors, errors[:3]

        # Connecting to a port nobody listens on fails cleanly and quickly.
        dead = free_port()
        t0 = time.monotonic()
        try:
            QMP(dead, connect_timeout=0.5)
            raise AssertionError("expected QMPError")
        except QMPError as e:
            print(f"no QEMU on port -> {str(e)[:60]}... after {time.monotonic() - t0:.1f}s")
        qmp.close()
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
