# QEMU MCP — The Hard Way

Build an MCP server that lets an AI agent boot a virtual machine, look at its screen,
type on its keyboard and read its serial console. Start from `subprocess.Popen` and
a raw socket, and end with the tools an agent actually calls. Every line explained.

What you build is [qemu-mcp](https://github.com/0xmortuex/qemu-mcp). This course isn't
a tidy reconstruction of it, though. It's organised around the bugs qemu-mcp actually
shipped and fixed:

- a qcow2 disk silently opened as raw
- temp directories leaked on every boot
- QMP replies swapped between threads
- error messages the agent never saw
- tests that passed on Linux and failed on Windows and macOS

Each one is reproduced here, and then fixed.

## Why This Exists

"Wrap a CLI tool in MCP" sounds like an afternoon. Then the VM is still booting when you
send keys, so they vanish. Your screenshot code never decides the screen has settled,
because a cursor blinks forever. Your error messages turn into "Error executing tool X".
And your test suite is green on your machine and red in CI.

None of that is about MCP. It's about driving a real system through its real
interfaces: a QMP socket that interleaves events with replies, a serial line that cares
whether you hang up, a framebuffer that is never quite still. This course is about that
part.

## Who This Is For

- Developers who've built a hello-world MCP server and want to build a real one
- OS and firmware developers who want an agent in their boot-test loop
- Anyone who wants to automate QEMU without a guest agent or SSH

**Prerequisites:** Python and basic sockets. You need [QEMU](https://www.qemu.org/download/)
(`qemu-system-i386` and `qemu-img`), plus `pip install -r requirements.txt` for Module 07.
Every module drives the course's own test kernel in [`kernel/`](kernel/). It is about 200
lines of C that prints to COM1, draws to the VGA text screen, echoes the keyboard, and
powers off on command.

## Modules

| # | Module | What You'll Build |
|---|--------|-------------------|
| 01 | [Launching QEMU Headless](01-launching-qemu-headless/) | Finding the binary on Linux and Windows, launching with no window, knowing when it booted |
| 02 | [QMP](02-qmp/) | A QMP client from scratch: greeting, capabilities, events between replies, the lock, timeouts |
| 03 | [Seeing the Screen](03-seeing-the-screen/) | screendump → PPM → PNG with no dependencies, and the blinking cursor that defeats "wait until stable" |
| 04 | [Typing](04-typing/) | Characters to QKeyCodes, shift and chords, and why you validate before you send |
| 05 | [The Serial Console](05-the-serial-console/) | A log you can read and a socket you can write, offset-based waits, and line endings you can't trust |
| 06 | [Snapshots](06-snapshots/) | savevm/loadvm through HMP, errors returned as success, and the qcow2-as-raw bug |
| 07 | [Tool Design](07-tool-design/) | The MCP layer: docstrings as prompts, results as state, and errors that reach the agent |
| 08 | [Lifecycle](08-lifecycle/) | Named VMs, quit vs kill, guests that power themselves off, and never leaking a temp directory |
| 09 | [Testing It For Real](09-testing-it-for-real/) | A fake QMP server on a real socket, and reproducing the Windows-only and macOS-only failures |

Each module contains:
- **`README.md`**: the concepts and design decisions
- **`main.py`**: a working, commented implementation that runs against real QEMU and checks itself with `assert`
- **`break-it.md`**: challenges that break your own code, with hints

## How To Use This Course

**Option A: Build along.** Read the modules in order, run each `main.py`, then do the
break-it challenges.

**Option B: Reference.** Jump to what you need. Each `main.py` stands alone; there are no
imports between modules.

**Option C: Use the finished tool.** `pip install qemu-mcp`, or read
[its source](https://github.com/0xmortuex/qemu-mcp) next to this course.

## Run It

```bash
python 04-typing/main.py --kernel kernel/kernel.elf     # one module
python verify.py                                         # all nine, against real QEMU
```

Every module starts its own QEMU, waits for it properly, asserts what its README claims,
and cleans up. CI runs `verify.py` on Ubuntu's QEMU on every push and weekly. All nine
modules were also verified on Windows with QEMU 11. Windows isn't in CI, because getting
QEMU onto a Windows runner is harder than one apt line.

## Architecture Overview

```
 AI agent
    │ MCP over stdio: tools/list, tools/call                  ← Module 07
    ▼
 ┌────────────────────────────────────────────────────────┐
 │ MCP server: named VMs, tools, ToolError boundary        │  ← Module 07, 08
 │                                                          │
 │  find & launch ──► qemu-system-i386 -display none ...    │  ← Module 01
 │  QMP client ─────► -qmp tcp:...  (JSON lines)            │  ← Module 02
 │      ├── screendump → PPM → PNG                          │  ← Module 03
 │      ├── send-key (QKeyCodes)                            │  ← Module 04
 │      └── human-monitor-command savevm/loadvm             │  ← Module 06
 │  serial ─────────► -chardev socket,...,logfile=...       │  ← Module 05
 └────────────────────────────────────────────────────────┘
                          │
                          ▼
               the guest: kernel/kernel.elf
```

## Key Design Principles

**1. Never act before the guest is ready.** Keys sent before the keyboard driver runs are
gone. Wait for a marker the guest prints, not a fixed sleep.

**2. A socket is a byte stream, not a message queue.** Replies split, events interleave,
connections reset. Parse and wait for what you need.

**3. Success can carry an error.** HMP returns failures as ordinary text, and screenshots
change when nothing happened. Check the meaning, not just the status.

**4. Every error is written for the agent.** Say what's wrong, what exists, and what to
call next, and make sure the message actually reaches the agent.

**5. Every path cleans up.** That includes the failure paths, and Windows, where you
can't delete a file a dead process still holds open.

## Tech Stack

- **Language:** Python 3.10+
- **Interfaces:** QEMU command line, QMP, HMP, socket chardevs, MCP over stdio
- **Dependencies:** `mcp` (Module 07 only); everything else is the standard library

## License

MIT. Use it, modify it, ship it.

[![M8ven Verified](https://m8ven.ai/badge/mcp/0xmortuex-qemu-mcp-the-hard-way-15dpgx?variant=verified&v=0fb34d88378c71dbc71d23abca7db0e3)](https://m8ven.ai/mcp/0xmortuex-qemu-mcp-the-hard-way-15dpgx?s=readme)

---

Built by [0xmortuex](https://github.com/0xmortuex)
