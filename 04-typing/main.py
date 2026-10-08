"""Module 04: typing into the guest - characters to QKeyCodes, shift, chords.

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
from typing import Any

# ---- characters -> qcodes ------------------------------------------------
# QMP's send-key takes QKeyCode names: physical keys, not characters. Typing
# "A" means pressing shift and "a" together. These tables are a US layout,
# which is what the guest's keyboard driver assumes too.
PLAIN = {
    " ": "spc", "\n": "ret", "\t": "tab", "-": "minus", "=": "equal",
    "[": "bracket_left", "]": "bracket_right", ";": "semicolon",
    "'": "apostrophe", "`": "grave_accent", "\\": "backslash",
    ",": "comma", ".": "dot", "/": "slash",
}
SHIFTED = {  # shifted character -> the unshifted key it lives on
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal",
    "{": "bracket_left", "}": "bracket_right", ":": "semicolon",
    '"': "apostrophe", "~": "grave_accent", "|": "backslash",
    "<": "comma", ">": "dot", "?": "slash",
}
NAMED = {
    "enter": "ret", "esc": "esc", "backspace": "backspace", "tab": "tab",
    "space": "spc", "up": "up", "down": "down", "left": "left", "right": "right",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift", "delete": "delete",
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
}


def char_to_keys(ch: str) -> list[str]:
    # The length check matters: str.islower() is true for "ab" too, so
    # without it a two-letter string would be "typed" as one bogus key.
    if len(ch) != 1:
        raise ValueError(f"expected one character, got {ch!r}")
    if ch.isascii() and (ch.islower() or ch.isdigit()):
        return [ch]
    if ch.isascii() and ch.isupper():
        return ["shift", ch.lower()]
    if ch in PLAIN:
        return [PLAIN[ch]]
    if ch in SHIFTED:
        return ["shift", SHIFTED[ch]]
    raise ValueError(f"cannot type {ch!r}: no key for it on a US keyboard")


def split_combo(raw: str) -> list[str]:
    """'ctrl-alt-f2' -> ['ctrl','alt','f2'].  '-' separates keys, so a literal
    minus key is written '--': 'ctrl--' -> ['ctrl', '-']."""
    parts, buf, i = [], "", 0
    while i < len(raw):
        if raw[i] == "-":
            if raw[i + 1:i + 2] == "-":
                if buf:
                    parts.append(buf)
                    buf = ""
                parts.append("-")
                i += 2
                continue
            if buf:
                parts.append(buf)
            buf = ""
        else:
            buf += raw[i]
        i += 1
    if buf:
        parts.append(buf)
    return parts


def parse_combo(combo: str) -> list[str]:
    keys = []
    for part in split_combo(combo.strip().lower()):
        if part in NAMED:
            keys.append(NAMED[part])
        elif len(part) == 1:
            keys.extend(char_to_keys(part))
        else:
            raise ValueError(f"unknown key {part!r} - use enter, esc, f1-f12, ctrl, alt, "
                             f"shift, arrows, or single characters")
    if not keys:
        raise ValueError("empty key combo")
    return keys


# ---- QMP (Module 02, condensed) and process helpers ---------------------
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


def send(qmp: QMP, keys: list[str], hold_ms: int = 50) -> None:
    """Press `keys` together, hold them, release them."""
    qmp.command("send-key", keys=[{"type": "qcode", "data": k} for k in keys],
                **{"hold-time": hold_ms})


def type_text(qmp: QMP, text: str, delay: float = 0.02) -> None:
    # Translate everything BEFORE sending anything: failing on the 4th
    # character after typing three would leave half a command in the guest.
    presses = [char_to_keys(ch) for ch in text]
    for keys in presses:
        send(qmp, keys)
        # Without a pause, a fast host can queue keys faster than a polling
        # guest drains its 1-byte PS/2 buffer, and characters get dropped.
        time.sleep(delay)


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


def wait_for(log: str, marker: bytes, timeout: float = 30) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(log, "rb") as fh:
                data = fh.read()
            if marker in data:
                return data
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"{marker!r} not on serial after {timeout}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    args = ap.parse_args()

    # Offline checks of the translation tables.
    assert char_to_keys("a") == ["a"] and char_to_keys("A") == ["shift", "a"]
    assert char_to_keys("!") == ["shift", "1"] and char_to_keys(" ") == ["spc"]
    assert parse_combo("ctrl-alt-f2") == ["ctrl", "alt", "f2"]
    assert parse_combo("ctrl--") == ["ctrl", "minus"]
    for bad in ("ab", ""):
        try:
            char_to_keys(bad)
            raise AssertionError(f"char_to_keys({bad!r}) should fail")
        except ValueError:
            pass
    try:
        type_text(None, "café")  # type: ignore[arg-type]
        raise AssertionError("é should be rejected before anything is sent")
    except ValueError as e:
        print(f"untypeable -> {e}")

    port = free_port()
    workdir = tempfile.mkdtemp(prefix="qemu-hard-way-04-")
    log = os.path.join(workdir, "serial.log")
    proc = subprocess.Popen(
        [find_qemu("i386"), "-display", "none", "-kernel", os.path.abspath(args.kernel),
         "-m", "64", "-serial", f"file:{log}",
         "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        qmp = QMP(port)
        wait_for(log, b"ready")          # keys sent before the driver runs are lost

        text = 'Hello, World! (x+y)*2 = "ok"'
        type_text(qmp, text + "\n")
        out = wait_for(log, b"you typed: ")
        out = wait_for(log, b"you typed: " + text.encode())
        print(f"kernel echoed: {out.split(b'you typed: ')[1].splitlines()[0]!r}")

        send(qmp, parse_combo("ctrl-c"))
        wait_for(log, b"[ctrl-c]")
        print("ctrl-c chord arrived as one keypress: [ctrl-c]")

        # Backspace: the guest erases the last character before Enter.
        type_text(qmp, "typo")
        send(qmp, ["backspace"])
        type_text(qmp, "\n")
        wait_for(log, b"you typed: typ\r")
        print("backspace edited the line: 'typo' -> 'typ'")
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
