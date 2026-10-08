"""Module 07: turning the pieces into MCP tools an agent can actually use.

Run:  python main.py --kernel ../kernel/kernel.elf
(The demo starts this same file as an MCP server over stdio, with --serve,
and drives it the way Claude Code would.)
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

# ---------------------------------------------------------------- engine
# Modules 01-05, condensed: find, boot, QMP, keys, serial.


def find_qemu(arch: str) -> str:
    exe = f"qemu-system-{arch}"
    hit = shutil.which(exe)
    if hit:
        return hit
    win = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "qemu", exe + ".exe")
    if os.path.isfile(win):
        return win
    raise FileNotFoundError(f"{exe} not found. Install QEMU and put it on PATH.")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


class QMP:
    def __init__(self, port: int):
        deadline = time.monotonic() + 20
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"QEMU never opened its QMP port {port}") from None
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


KEYS = {" ": "spc", "\n": "ret", "-": "minus", ".": "dot", ",": "comma", "/": "slash"}


def char_to_keys(ch: str) -> list[str]:
    if ch.isascii() and (ch.islower() or ch.isdigit()):
        return [ch]
    if ch.isascii() and ch.isupper():
        return ["shift", ch.lower()]
    if ch in KEYS:
        return [KEYS[ch]]
    raise ValueError(f"cannot type {ch!r} - letters, digits, space, newline and - . , / only")


@dataclass
class VM:
    name: str
    proc: subprocess.Popen[bytes]
    qmp: QMP
    workdir: str
    log: str


VMS: dict[str, VM] = {}


def get_vm(name: str) -> VM:
    vm = VMS.get(name)
    if vm is None:
        running = ", ".join(sorted(VMS)) or "none"
        # Three parts: what's wrong, what exists, what to do.
        raise KeyError(f"no VM named {name!r} (running: {running}) - call qemu_boot first")
    if vm.proc.poll() is not None:
        raise RuntimeError(f"VM {name!r} has exited (code {vm.proc.returncode}). "
                           f"Last serial output: {read_log(vm)[-200:]!r}")
    return vm


def read_log(vm: VM) -> str:
    try:
        with open(vm.log, "rb") as fh:
            return fh.read().replace(b"\r", b"").decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


# ------------------------------------------------------------ MCP layer
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

mcp = MCPServer(
    "qemu-lite",
    instructions=(
        "Boot and drive a QEMU VM headless. Loop: qemu_boot -> qemu_wait_serial "
        "for a boot marker -> qemu_type -> qemu_wait_serial -> qemu_stop."
    ),
)

F = TypeVar("F", bound=Callable[..., Any])

# MCPServer forwards ONLY a ToolError's message to the client. Anything else
# becomes "Error executing tool X" - our instructions vanish. Failures the
# agent can act on are converted here, at the tool boundary; genuine bugs
# (anything not in this tuple) stay hidden, as the SDK intends.
EXPECTED = (KeyError, ValueError, FileNotFoundError, RuntimeError, TimeoutError, OSError)


def tool(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EXPECTED as e:
            msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
            raise ToolError(str(msg)) from e

    mcp.tool()(wrapper)
    return fn


@tool
def qemu_boot(name: str, kernel: str, memory_mb: int = 64) -> str:
    """Boot a multiboot kernel headless and register it under `name`.

    Serial output is captured from the start; use qemu_wait_serial to know
    when it has booted. The VM runs until qemu_stop.
    """
    if not name or any(c in name for c in "/\\") or name in (".", ".."):
        raise ValueError(f"invalid VM name {name!r}: it names a temp directory - no slashes")
    if name in VMS:
        raise RuntimeError(f"a VM named {name!r} is already running - qemu_stop it first")
    if not os.path.isfile(kernel):
        raise FileNotFoundError(f"kernel not found: {kernel}")
    workdir = tempfile.mkdtemp(prefix=f"qemu-lite-{name}-")
    log = os.path.join(workdir, "serial.log")
    port = free_port()
    proc = subprocess.Popen(
        [find_qemu("i386"), "-display", "none", "-kernel", kernel, "-m", str(memory_mb),
         "-serial", f"file:{log}", "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        qmp = QMP(port)
    except RuntimeError:
        proc.kill()
        proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    VMS[name] = VM(name, proc, qmp, workdir, log)
    return (f"VM {name!r} booted (pid {proc.pid}). "
            f"Next: qemu_wait_serial(name={name!r}, marker=...) for a boot marker.")


@tool
def qemu_wait_serial(name: str, marker: str, timeout_s: float = 30) -> str:
    """Wait until `marker` appears on the VM's serial console.

    Returns the serial output. On timeout the VM keeps running and the
    output so far is included, so you can see what it printed instead.
    """
    vm = get_vm(name)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = read_log(vm)
        if marker in text:
            return f"FOUND {marker!r}.\n{text[-1500:]}"
        if vm.proc.poll() is not None:
            get_vm(name)                       # raises the "has exited" error
        time.sleep(0.05)
    return (f"TIMEOUT after {timeout_s}s: {marker!r} not seen. The VM is still running. "
            f"Serial so far:\n{read_log(vm)[-1500:]}")


@tool
def qemu_type(name: str, text: str) -> str:
    """Type text on the VM's keyboard ("\\n" presses Enter)."""
    vm = get_vm(name)
    presses = [char_to_keys(c) for c in text]   # validate everything first
    for keys in presses:
        vm.qmp.command("send-key", keys=[{"type": "qcode", "data": k} for k in keys])
        time.sleep(0.02)
    return f"typed {len(text)} characters into {name!r}"


@tool
def qemu_stop(name: str) -> str:
    """Stop the VM and delete its temporary files."""
    vm = get_vm(name)
    vm.proc.kill()
    vm.proc.wait()
    shutil.rmtree(vm.workdir, ignore_errors=True)
    del VMS[name]
    return f"stopped {name!r}"


@mcp.tool()
def demo_unwrapped_error(name: str) -> str:
    """Demo only: raises the same KeyError as get_vm, WITHOUT the ToolError
    conversion, to show what the agent receives."""
    return get_vm(name).name


# ----------------------------------------------------------------- demo
def demo(kernel: str) -> int:
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run() -> None:
        params = StdioServerParameters(command=sys.executable,
                                       args=[os.path.abspath(__file__), "--serve"])
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            print(f"tools: {sorted(tools)}")
            # The docstring IS the description the agent reads.
            assert "qemu_wait_serial" in (tools["qemu_boot"].description or "")
            props = tools["qemu_boot"].input_schema["properties"]
            print(f"qemu_boot schema params: {list(props)} (memory_mb default {props['memory_mb']['default']})")

            async def call(tool_name: str, **a: Any) -> tuple[bool, str]:
                res = await s.call_tool(tool_name, a)
                return bool(res.is_error), res.content[0].text  # type: ignore[union-attr]

            err, text = await call("qemu_boot", name="k", kernel=kernel)
            print(f"> qemu_boot\n{text}")
            assert not err
            err, text = await call("qemu_wait_serial", name="k", marker="ready")
            assert not err and text.startswith("FOUND")
            err, text = await call("qemu_type", name="k", text="hello agent\n")
            assert not err
            err, text = await call("qemu_wait_serial", name="k", marker="you typed: hello agent")
            print("> qemu_type + qemu_wait_serial: the guest echoed 'you typed: hello agent'")
            assert not err

            # The same mistake through both paths.
            err, raw = await call("demo_unwrapped_error", name="kernel")
            err2, good = await call("qemu_type", name="kernel", text="x")
            print(f"unwrapped KeyError reaches the agent as: {raw!r}")
            print(f"ToolError reaches the agent as:          {good!r}")
            assert err and "no VM named" not in raw
            assert err2 and "no VM named 'kernel' (running: k) - call qemu_boot first" in good

            err, text = await call("qemu_type", name="k", text="café")
            print(f"untypeable text -> {text}")
            assert err and "cannot type" in text

            err, text = await call("qemu_wait_serial", name="k", marker="never", timeout_s=1)
            assert not err and text.startswith("TIMEOUT")
            print("a timeout is a result, not an error: 'TIMEOUT ... The VM is still running'")

            err, text = await call("qemu_stop", name="k")
            assert not err

    anyio.run(run)
    print("OK")
    return 0


if __name__ == "__main__":
    if "--serve" in sys.argv:
        mcp.run()
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--kernel", required=True)
        sys.exit(demo(os.path.abspath(ap.parse_args().kernel)))
