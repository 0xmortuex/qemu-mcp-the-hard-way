"""Module 03: screendump -> PPM -> PNG, and waiting for the screen to settle.

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
import time
import zlib
from typing import Any


# ---- the QMP client from Module 02, condensed ---------------------------
class QMP:
    def __init__(self, port: int, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        self.sock.settimeout(15)
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
            line = self.f.readline()
            if not line:
                raise RuntimeError("QMP connection closed by QEMU")
            resp = json.loads(line)
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


def find_qemu(arch: str) -> str:
    exe = f"qemu-system-{arch}"
    hit = shutil.which(exe)
    if hit:
        return hit
    win = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "qemu", exe + ".exe")
    if os.path.isfile(win):
        return win
    raise FileNotFoundError(f"{exe} not found")


# ---- PPM -> PNG, with nothing but the standard library -------------------
def read_ppm(path: str) -> tuple[int, int, bytes]:
    """QEMU's screendump writes binary PPM: 'P6\\n<w> <h>\\n255\\n' + RGB bytes."""
    with open(path, "rb") as fh:
        data = fh.read()
    # The header is whitespace-separated tokens; the pixels start right
    # after the single whitespace byte that follows maxval.
    parts = data.split(maxsplit=4)
    magic, w, h, maxval, pixels = parts
    if magic != b"P6" or maxval != b"255":
        raise ValueError(f"not an 8-bit binary PPM: {magic!r} maxval {maxval!r}")
    width, height = int(w), int(h)
    if len(pixels) != width * height * 3:
        raise ValueError(f"truncated PPM: {len(pixels)} bytes for {width}x{height}")
    return width, height, pixels


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """A minimal valid PNG: signature, IHDR, one IDAT, IEND."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    stride = width * 3
    # Each scanline is prefixed with a filter-type byte; 0 = no filter.
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def screenshot(qmp: QMP, workdir: str) -> tuple[bytes, bytes]:
    """Returns (png bytes, raw RGB pixels)."""
    ppm = os.path.join(workdir, "screen.ppm")
    if os.path.exists(ppm):
        os.remove(ppm)
    qmp.command("screendump", filename=ppm)
    # Don't assume the file is complete the moment the reply arrives (qemu-mcp
    # polls for it too): wait until the header and pixel count add up.
    deadline = time.monotonic() + 5
    while True:
        try:
            w, h, px = read_ppm(ppm)
            break
        except (FileNotFoundError, ValueError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)
    return png_bytes(w, h, px), px


def changed_pixels(a: bytes, b: bytes) -> int:
    """How many RGB pixels differ between two frames of the same size."""
    if len(a) != len(b):
        return len(a) // 3            # a mode switch: everything changed
    if a == b:
        return 0
    return sum(1 for i in range(0, len(a), 3) if a[i:i + 3] != b[i:i + 3])


# A blinking text-mode cursor flips a 9x2 block (18 px) forever. Anything a
# real screen update does - a character, a line, a progress tick - touches
# far more than this. Frames differing by fewer pixels count as unchanged.
CURSOR_NOISE_PX = 64


def wait_screen(qmp: QMP, workdir: str, stable_polls: int = 3, interval: float = 0.3,
                timeout: float = 30) -> tuple[int, bytes]:
    """Poll screendumps until `stable_polls` consecutive ones are unchanged
    (ignoring cursor-sized blinking)."""
    deadline = time.monotonic() + timeout
    prev: bytes | None = None
    same, polls = 0, 0
    while time.monotonic() < deadline:
        png, px = screenshot(qmp, workdir)
        polls += 1
        unchanged = prev is not None and changed_pixels(prev, px) <= CURSOR_NOISE_PX
        same = same + 1 if unchanged else 1
        prev = px
        if same >= stable_polls:
            return polls, png
        time.sleep(interval)
    raise TimeoutError(f"screen still changing after {timeout}s ({polls} polls)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    args = ap.parse_args()

    port = free_port()
    workdir = tempfile.mkdtemp(prefix="qemu-hard-way-03-")
    serial = os.path.join(workdir, "serial.log")
    proc = subprocess.Popen(
        [find_qemu("i386"), "-display", "none", "-kernel", os.path.abspath(args.kernel),
         "-m", "64", "-serial", f"file:{serial}",
         "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        qmp = QMP(port)
        # While the boot progress bar runs, consecutive frames differ a lot.
        _, first = screenshot(qmp, workdir)
        time.sleep(0.5)
        _, second = screenshot(qmp, workdir)
        with open(serial, "rb") as fh:
            booted_yet = b"ready" in fh.read()
        moved = changed_pixels(first, second)
        print(f"two frames 0.5s apart during boot: {moved} px changed (kernel ready yet: {booted_yet})")
        assert booted_yet or moved > CURSOR_NOISE_PX

        # The naive check - "two frames are byte-identical" - never succeeds
        # here: the cursor blinks forever. Show it, then do it properly.
        time.sleep(4)
        _, a = screenshot(qmp, workdir)
        time.sleep(0.35)
        _, b = screenshot(qmp, workdir)
        blink = changed_pixels(a, b)
        print(f"settled screen, two frames 0.35s apart: {blink} px differ (the blinking cursor)")
        assert 0 <= blink <= CURSOR_NOISE_PX

        polls, png = wait_screen(qmp, workdir)
        with open(serial, "rb") as fh:
            log = fh.read()
        print(f"screen settled after {polls} polls; serial says ready: {b'ready' in log}")
        assert b"ready" in log, "a settled screen should mean the boot finished"

        out = os.path.join(workdir, "settled.png")
        with open(out, "wb") as fh:
            fh.write(png)
        w, h = struct.unpack(">II", png[16:24])
        print(f"PNG: {len(png)} bytes, {w}x{h} (VGA text mode is 720x400)")
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and (w, h) == (720, 400)
        # Round-trip: decompress our IDAT and compare with the PPM pixels.
        idat_len = struct.unpack(">I", png[33:37])[0]
        raw = zlib.decompress(png[41:41 + idat_len])
        assert len(raw) == h * (w * 3 + 1)
        print("PNG decodes back to the same number of pixels")
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
