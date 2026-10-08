# Module 07: Tool Design

## The Concept

Modules 01 to 06 built an engine: find QEMU, boot it, talk QMP, see, type, read serial,
snapshot. None of it is usable by an agent yet. This module adds the MCP layer, and most
of it isn't plumbing, it's design. An agent reading your tools has three handicaps:

- **It sees only what you return.** No screen, no log tail unless you include one.
- **It reads your docstrings as instructions.** They're the tool descriptions it
  chooses from.
- **It will make mistakes**: wrong VM names, untypeable text, waiting for a marker that
  never comes. The error is its only clue what to do next.

`main.py` is a real five-tool MCP server. Its demo starts the same file with `--serve`
over stdio and drives it with the SDK's client, so every claim below is something an
actual MCP client observed.

## The Server

```python
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "qemu-lite",
    instructions=(
        "Boot and drive a QEMU VM headless. Loop: qemu_boot -> qemu_wait_serial "
        "for a boot marker -> qemu_type -> qemu_wait_serial -> qemu_stop."
    ),
)
```

`instructions` is the one place to describe the **workflow**, not just one tool. It's
how an agent learns that `qemu_wait_serial` comes before `qemu_type`. That's Module 01's
"never act before ready" rule, turned into guidance.

This is the `mcp` 2.x API. qemu-mcp started on 1.x's `FastMCP`. When `mcp` 2.0 shipped,
an unpinned `mcp>=1.2` dependency resolved to it, and `from mcp.server.fastmcp import
FastMCP` stopped existing. A fresh `pip install qemu-mcp` failed at import. The
emergency fix pinned `<2.0`; the migration came later. In 2.x, `MCPServer` is a
near-drop-in rename. Either way, an unpinned major version of your core dependency is a
time bomb.

## Docstrings Are the Prompt

```python
@tool
def qemu_boot(name: str, kernel: str, memory_mb: int = 64) -> str:
    """Boot a multiboot kernel headless and register it under `name`.

    Serial output is captured from the start; use qemu_wait_serial to know
    when it has booted. The VM runs until qemu_stop.
    """
```

The SDK turns the signature into a JSON schema (`name`, `kernel`, `memory_mb` with
default 64) and the docstring into the description. The demo asserts both reach the
client. Write the docstring for a reader deciding *whether* to call the tool and *what
comes next*. Name parameters with their units: `timeout_s`, not `timeout`.

## Results Are State, Not Status

```
VM 'k' booted (pid 16724). Next: qemu_wait_serial(name='k', marker=...) for a boot marker.
```

"OK" tells the agent nothing. This result says what exists now, how to identify it,
and the next useful call.

Timeouts get the same treatment. `qemu_wait_serial` returns **TIMEOUT as a result, not
an error**: "TIMEOUT after 1s: 'never' not seen. The VM is still running. Serial so
far: ..." Nothing is broken. The guest just didn't say that particular thing, and the
output it *did* produce is right there to read.

## Errors That Reach the Agent

Here is the most important part of this module, and qemu-mcp shipped with it wrong.

A tool's error message should have three parts: **what's wrong**, **what exists**, and
**what to do**:

```python
raise KeyError(f"no VM named {name!r} (running: {running}) - call qemu_boot first")
```

Now look at what the agent receives. The demo raises that same `KeyError` from two
tools. One is registered plainly with `@mcp.tool()`, the other through this module's
`@tool` wrapper:

```
unwrapped KeyError reaches the agent as: 'Error executing tool demo_unwrapped_error'
ToolError reaches the agent as:          "Error executing tool qemu_type: no VM named 'kernel' (running: k) - call qemu_boot first"
```

`MCPServer` forwards the message **only** of a `ToolError`. Any other exception is
treated as a crash, and the client gets the bare tool name. That's deliberate: crash
text can leak internals. But it means every carefully written error in qemu-mcp, such as
"VM has exited. Last serial output: ..." or "qemu-system-foo not found. Found on this
system: ...", arrived as "Error executing tool qemu_boot". Its unit tests called the
functions directly and saw the full messages, so nothing caught it until a test spoke
real MCP over stdio.

The fix is one wrapper at the tool boundary:

```python
EXPECTED = (KeyError, ValueError, FileNotFoundError, RuntimeError, TimeoutError, OSError)

def tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EXPECTED as e:
            msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
            raise ToolError(str(msg)) from e
    mcp.tool()(wrapper)
    return fn
```

- **`functools.wraps`** keeps the signature and docstring, which the SDK reads to build
  the schema.
- **`KeyError`'s `str()`** wraps the message in quotes, so it uses `args[0]` instead.
- **Only expected failures are converted.** An `AttributeError` from a bug still
  becomes a hidden crash, as the SDK intends. This is not "catch everything and return a
  string".

## Validation at the Boundary

`qemu_boot` checks the name before touching the filesystem: no slashes, not `.`, not
`..`. It checks the kernel file exists before launching QEMU. If QMP never comes up,
it kills QEMU and deletes the work directory before re-raising. Each check turns a
confusing low-level failure, such as `FileNotFoundError` from `mkdtemp` or a QEMU
stderr dump, into a sentence the agent can act on.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
tools: ['demo_unwrapped_error', 'qemu_boot', 'qemu_stop', 'qemu_type', 'qemu_wait_serial']
qemu_boot schema params: ['name', 'kernel', 'memory_mb'] (memory_mb default 64)
> qemu_boot
VM 'k' booted (pid 16724). Next: qemu_wait_serial(name='k', marker=...) for a boot marker.
> qemu_type + qemu_wait_serial: the guest echoed 'you typed: hello agent'
unwrapped KeyError reaches the agent as: 'Error executing tool demo_unwrapped_error'
ToolError reaches the agent as:          "Error executing tool qemu_type: no VM named 'kernel' (running: k) - call qemu_boot first"
untypeable text -> Error executing tool qemu_type: cannot type 'é' - letters, digits, space, newline and - . , / only
a timeout is a result, not an error: 'TIMEOUT ... The VM is still running'
OK
```

The server's stderr shows a traceback for `demo_unwrapped_error`. That's where crash
details go: to your log, not the agent.
