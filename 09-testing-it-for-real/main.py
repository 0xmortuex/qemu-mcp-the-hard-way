"""Module 09: testing - a fake QMP server on a real socket, and the bugs that
only show up on Windows or macOS.

Run:  python main.py --kernel ../kernel/kernel.elf
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any


# ---- the client under test: Module 02's QMP, plus a quit() ---------------
class QMPError(RuntimeError):
    pass


class QMP:
    def __init__(self, port: int, read_timeout: float = 5.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(read_timeout)
        self.buf = b""
        self.events: list[str] = []
        self._read()
        self.command("qmp_capabilities")

    def _read(self) -> dict[str, Any]:
        while b"\n" not in self.buf:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                raise QMPError("QMP read timed out - QEMU stopped responding") from None
            except (ConnectionResetError, ConnectionAbortedError) as e:
                # Windows reports a peer close as WinError 10053/10054 where
                # Linux usually gives a clean EOF. Same meaning, same message.
                raise QMPError(f"QMP connection closed by QEMU ({e})") from None
            if not chunk:
                raise QMPError("QMP connection closed by QEMU")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        msg: dict[str, Any] = json.loads(line)
        return msg

    def command(self, name: str, **arguments: Any) -> Any:
        msg: dict[str, Any] = {"execute": name}
        if arguments:
            msg["arguments"] = arguments
        self.sock.sendall(json.dumps(msg).encode() + b"\n")
        while True:
            resp = self._read()
            if "event" in resp:
                self.events.append(resp["event"])
                continue
            if "error" in resp:
                raise QMPError(f"{name}: {resp['error']['desc']}")
            return resp["return"]


# ---- a fake QEMU: real TCP, scripted behaviour --------------------------
GREETING = b'{"QMP": {"version": {"qemu": {"major": 9, "minor": 0, "micro": 0}}, "capabilities": []}}\n'


class FakeQMP:
    """A thread that accepts one client and runs `script(conn)`.

    Mocks would replace the socket, and the socket is where these bugs live:
    replies split across reads, events between a command and its reply,
    peers that reset instead of closing. A real loopback socket is cheap and
    behaves like the real thing on every OS.
    """

    def __init__(self, script: Callable[[socket.socket], None]):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port: int = self.srv.getsockname()[1]
        self.script = script
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        conn, _ = self.srv.accept()
        try:
            conn.sendall(GREETING)
            self.script(conn)
        except OSError:
            pass  # the client went away; that's the test's business
        finally:
            conn.close()
            self.srv.close()


def read_cmd(conn: socket.socket) -> dict[str, Any]:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(1)
        if not chunk:
            raise OSError("client closed")
        buf += chunk
    cmd: dict[str, Any] = json.loads(buf)
    return cmd


def ok(conn: socket.socket, value: Any = None) -> None:
    conn.sendall(json.dumps({"return": {} if value is None else value}).encode() + b"\n")


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" - {detail}" if detail else ""))
    assert cond, name


def test_event_between_command_and_reply() -> None:
    def script(conn: socket.socket) -> None:
        read_cmd(conn)
        ok(conn)                                   # qmp_capabilities
        read_cmd(conn)                             # query-status
        conn.sendall(b'{"event": "RESUME", "data": {}}\n')
        ok(conn, {"status": "running", "running": True})

    q = QMP(FakeQMP(script).port)
    r = q.command("query-status")
    check("an event between command and reply is filed, not returned",
          r["running"] is True and q.events == ["RESUME"])


def test_reply_split_across_reads() -> None:
    def script(conn: socket.socket) -> None:
        read_cmd(conn)
        ok(conn)
        read_cmd(conn)
        reply = b'{"return": {"name": "split"}}\n'
        for i in range(0, len(reply), 3):          # 3 bytes per segment
            conn.sendall(reply[i:i + 3])
            time.sleep(0.005)

    q = QMP(FakeQMP(script).port)
    check("a reply delivered in 3-byte pieces is reassembled",
          q.command("query-name") == {"name": "split"})


def test_reset_is_reported_as_closed() -> None:
    def script(conn: socket.socket) -> None:
        read_cmd(conn)
        ok(conn)
        read_cmd(conn)
        # SO_LINGER(on, 0): close() sends RST instead of FIN - what Windows
        # often reports when QEMU exits. Reproduces the Windows-only CI
        # failure on every OS.
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))

    q = QMP(FakeQMP(script).port)
    try:
        q.command("query-status")
        check("a reset connection raises", False)
    except QMPError as e:
        check("a reset connection says 'closed by QEMU' on every OS",
              "closed by QEMU" in str(e), str(e)[:70])


def test_silence_times_out() -> None:
    def script(conn: socket.socket) -> None:
        read_cmd(conn)
        ok(conn)
        read_cmd(conn)
        time.sleep(3)                              # never answers

    q = QMP(FakeQMP(script).port, read_timeout=0.5)
    t0 = time.monotonic()
    try:
        q.command("query-status")
        check("silence raises", False)
    except QMPError as e:
        check("a silent QEMU times out instead of hanging",
              "timed out" in str(e) and time.monotonic() - t0 < 2.5)


def test_text_mode_files() -> None:
    # qemu-mcp's Windows-only failure: a test wrote its fake serial log with
    # write_text("boot ok\n") and compared bytes. On Windows, text mode
    # wrote "\r\n". QEMU writes the real log in binary, so write bytes.
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "x.log")
        with open(p, "w") as fh:
            fh.write("boot ok\n")
        text_size = os.path.getsize(p)
        with open(p, "wb") as fh:
            fh.write(b"boot ok\n")
        check(f"text mode wrote {text_size} bytes on {sys.platform}; binary always writes 8",
              os.path.getsize(p) == 8 and text_size == (9 if sys.platform == "win32" else 8))
    finally:
        shutil.rmtree(d)


def test_two_sends_may_arrive_separately() -> None:
    # qemu-mcp's macOS-only failure: a test sent "a" then "b" and asserted a
    # single recv() returned b"ab". TCP is a byte stream: read until done.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(srv.getsockname())
    s, _ = srv.accept()
    c.sendall(b"a")
    time.sleep(0.05)
    c.sendall(b"b")
    first = s.recv(1024)
    got = first
    while len(got) < 2:
        got += s.recv(1024)
    check(f"first recv() returned {first!r}; reading until 2 bytes gives b'ab'", got == b"ab")
    for x in (c, s, srv):
        x.close()


def live_quit(kernel: str) -> None:
    """Against real QEMU: quit's reply may or may not arrive before the
    socket closes (8.2 closes first, 11.0 replies). Accept either; insist
    the process exits."""
    exe = shutil.which("qemu-system-i386") or os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "qemu", "qemu-system-i386.exe")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([exe, "-display", "none", "-kernel", kernel, "-m", "64",
                             "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                q = QMP(port)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        version = q.command("query-version")["qemu"]
        try:
            q.command("quit")
            outcome = "replied, then closed"
        except QMPError:
            outcome = "closed without replying"
        proc.wait(timeout=10)
        check(f"real QEMU {version['major']}.{version['minor']} quit: {outcome}; process exited",
              proc.returncode is not None)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    args = ap.parse_args()
    print("fake QMP server, real sockets:")
    test_event_between_command_and_reply()
    test_reply_split_across_reads()
    test_reset_is_reported_as_closed()
    test_silence_times_out()
    print("cross-platform lessons:")
    test_text_mode_files()
    test_two_sends_may_arrive_separately()
    print("real QEMU:")
    live_quit(os.path.abspath(args.kernel))
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
