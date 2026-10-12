"""Tests for Module 07's MCP server, spoken to over real MCP stdio.

Each test starts 07-tool-design/main.py with --serve and calls it through the
SDK's client, so they see exactly what an agent sees. Calling the functions
directly would miss the ToolError conversion (see Module 07).

    pip install -r requirements.txt pytest
    pytest tests

test_full_lifecycle boots the course kernel, so it needs qemu-system-i386.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "07-tool-design", "main.py")
KERNEL = os.path.join(ROOT, "kernel", "kernel.elf")

# What each tool does to the world. Changing a tool's behaviour without
# updating its hints (or the other way round) fails here.
HINTS = {
    #                    read_only destructive idempotent open_world
    "qemu_boot":        (False,    False,      False,     False),
    "qemu_wait_serial": (True,     False,      True,      False),
    "qemu_type":        (False,    True,       False,     False),
    "qemu_stop":        (False,    True,       True,      False),
    "demo_unwrapped_error": (True, False,      True,      False),
}

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def server() -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=sys.executable, args=[SERVER, "--serve"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        yield s


async def call(s: ClientSession, tool: str, **args: Any) -> tuple[bool, str]:
    res = await s.call_tool(tool, args)
    return bool(res.is_error), res.content[0].text  # type: ignore[union-attr]


async def test_every_tool_declares_all_four_hints() -> None:
    async with server() as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == set(HINTS)
    for name, want in HINTS.items():
        a = tools[name].annotations
        assert a is not None, name
        got = (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)
        assert got == want, name


async def test_boot_rejects_bad_input_before_launching_anything() -> None:
    async with server() as s:
        err, text = await call(s, "qemu_boot", name="../x", kernel=KERNEL)
        assert err and "invalid VM name" in text
        err, text = await call(s, "qemu_boot", name="k", kernel="no-such-kernel.elf")
        assert err and "kernel not found: no-such-kernel.elf" in text


async def test_tools_on_an_unknown_vm_say_what_to_do() -> None:
    async with server() as s:
        for tool, extra in (("qemu_wait_serial", {"marker": "x", "timeout_s": 1}),
                            ("qemu_type", {"text": "x"}),
                            ("qemu_stop", {})):
            err, text = await call(s, tool, name="ghost", **extra)
            assert err, tool
            assert "no VM named 'ghost' (running: none) - call qemu_boot first" in text, tool


async def test_unwrapped_errors_hide_their_message() -> None:
    async with server() as s:
        err, text = await call(s, "demo_unwrapped_error", name="ghost")
    assert err and "no VM named" not in text


async def test_full_lifecycle() -> None:
    async with server() as s:
        err, text = await call(s, "qemu_boot", name="t", kernel=KERNEL)
        assert not err, text
        err, text = await call(s, "qemu_boot", name="t", kernel=KERNEL)
        assert err and "already running" in text

        err, text = await call(s, "qemu_wait_serial", name="t", marker="ready")
        assert not err and text.startswith("FOUND"), text
        err, text = await call(s, "qemu_type", name="t", text="tested\n")
        assert not err, text
        err, text = await call(s, "qemu_wait_serial", name="t", marker="you typed: tested")
        assert not err and text.startswith("FOUND"), text

        err, text = await call(s, "qemu_wait_serial", name="t", marker="never", timeout_s=1)
        assert not err and text.startswith("TIMEOUT"), text

        err, text = await call(s, "qemu_stop", name="t")
        assert not err and text == "stopped 't'"
        # A second stop changes nothing (idempotent), and says why.
        err, text = await call(s, "qemu_stop", name="t")
        assert err and "no VM named 't'" in text
