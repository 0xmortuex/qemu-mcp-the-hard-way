"""Module 05: the serial console - a log you can read AND a socket you can write.

Run:  python main.py --kernel ../kernel/kernel.elf
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time


def chardev_args(log: str, port: int) -> list[str]:
    """One chardev, two outlets.

    `-serial file:` is write-only from the guest's side: nothing can be sent
    back. A TCP socket chardev is bidirectional, and `logfile=` keeps a
    complete copy of everything the guest printed - even while no client is
    connected - so reading the log never depends on the socket.
    """
    return [
        "-chardev",
        f"socket,id=serial0,host=127.0.0.1,port={port},server=on,wait=off,"
        f"logfile={log},logappend=on",
        "-serial", "chardev:serial0",
    ]


class SerialConsole:
    """A persistent connection to the guest's COM1.

    Connect once and keep it. When a client disconnects, the socket chardev
    reports the line as closed to the emulated UART; a guest that watches
    carrier (a getty) treats that as a hangup and can kill the login shell.
    qemu-mcp reconnects only if a write fails.
    """

    def __init__(self, port: int):
        self.port = port
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()

    def send(self, text: str) -> None:
        with self.lock:
            if self.sock is None:
                self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                self.sock.sendall(text.encode())
            except OSError:
                self.sock.close()
                self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                self.sock.sendall(text.encode())

    def close(self) -> None:
        with self.lock:
            if self.sock is not None:
                self.sock.close()
                self.sock = None


def normalize(data: bytes) -> str:
    """Serial line endings vary. The kernel sends \\r\\n; on Linux the logfile
    has exactly that, but with QEMU 11 on Windows we observed \\r\\r\\n for
    most lines of a socket chardev's logfile (and plain \\r\\n with
    `-serial file:`). Don't depend on line endings: match against text with
    every \\r removed."""
    return data.replace(b"\r", b"").decode("utf-8", errors="replace")


def log_size(log: str) -> int:
    try:
        return os.path.getsize(log)
    except FileNotFoundError:
        return 0


def wait_serial(log: str, marker: str, since: int = 0, timeout: float = 30) -> tuple[str, int]:
    """Wait for `marker` in output written AFTER byte offset `since`.

    The offset is what makes repeated waits correct: after typing a second
    command, the answer to the first one is still in the log, and a plain
    "is it in the file?" check would match it immediately.
    Returns (new text, new offset).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(log, "rb") as fh:
                fh.seek(since)
                new = fh.read()
        except FileNotFoundError:
            new = b""
        if marker in normalize(new):
            return normalize(new), since + len(new)
        time.sleep(0.05)
    raise TimeoutError(f"{marker!r} not on serial within {timeout}s (after offset {since})")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def find_qemu(arch: str) -> str:
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

    assert normalize(b"ok\r\r\n") == normalize(b"ok\r\n") == normalize(b"ok\n") == "ok\n"

    port = free_port()
    workdir = tempfile.mkdtemp(prefix="qemu-hard-way-05-")
    # QEMU parses chardev options with commas and backslashes: give it a
    # forward-slash path so a Windows path survives.
    log = os.path.join(workdir, "serial.log").replace("\\", "/")
    proc = subprocess.Popen(
        [find_qemu("i386"), "-display", "none", "-kernel", os.path.abspath(args.kernel),
         "-m", "64", *chardev_args(log, port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    console = SerialConsole(port)
    try:
        # No client is connected during boot, but the logfile still has it all.
        text, offset = wait_serial(log, "ready")
        print(f"boot output (from the logfile, no client connected): {text!r}")
        with open(log, "rb") as fh:
            raw = fh.read()
        print(f"raw bytes on disk end with: {raw[-12:]!r}")

        console.send("ping\r")
        text, offset = wait_serial(log, "serial got: ping", since=offset)
        print("sent 'ping' over the socket -> guest replied 'serial got: ping'")

        # Send a second line. The first reply is still in the log; waiting
        # from the new offset must not match it.
        console.send("pong\r")
        text, offset = wait_serial(log, "serial got: pong", since=offset)
        assert "serial got: ping" not in text, "offset wait matched stale output"
        print("second wait saw only new output (stale 'ping' reply skipped)")

        # The same connection is reused: still exactly one socket.
        first = console.sock
        console.send("count 3\r")
        assert console.sock is first
        wait_serial(log, "count=3", since=offset)
        print("one persistent connection for every send")

        # The client also RECEIVES what the guest prints while it's connected.
        assert console.sock is not None
        console.sock.settimeout(3)
        console.send("echo-me\r")
        got = b""
        deadline = time.monotonic() + 5
        while b"serial got: echo-me" not in got and time.monotonic() < deadline:
            try:
                got += console.sock.recv(4096)
            except TimeoutError:
                break
        assert b"serial got: echo-me" in got.replace(b"\r", b"")
        print("the socket carries guest output too - it is a real terminal line")
    finally:
        console.close()
        proc.kill()
        proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
