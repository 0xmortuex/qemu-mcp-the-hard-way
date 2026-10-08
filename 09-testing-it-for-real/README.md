# Module 09: Testing It For Real

## The Concept

qemu-mcp's CI ran 330+ tests on Linux, Windows and macOS. For a while, three of them
failed only on Windows or only on macOS, while passing on Linux, where most development
and review happened. None of the three was a QEMU bug, and all three came from one
assumption: that the operating system behaves like the one you developed on.

This module builds the test setup that catches those bugs: a **fake QMP server on a real
socket**, plus one check against real QEMU. It also reproduces each cross-platform
failure deliberately.

## Why Not Mock the Socket?

The obvious unit test replaces `socket.recv` with a mock that returns whole JSON
messages. It's fast, and it tests nothing that matters. Every bug in Modules 02 and 05
lives in the socket:

- replies split across reads
- events between a command and its reply
- peers that reset instead of closing
- servers that go silent

A mock returns exactly what you told it to, so it can't surprise you.

A loopback TCP server is nearly as cheap and behaves like the real thing on every OS:

```python
class FakeQMP:
    def __init__(self, script):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))      # port 0: the OS picks a free one
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        conn, _ = self.srv.accept()
        conn.sendall(GREETING)
        self.script(conn)                    # the test decides what QEMU "does"
```

Each test passes a short script: read a command, send an event, reply. The client
under test is Module 02's, unchanged.

## Four Protocol Tests

```
PASS  an event between command and reply is filed, not returned
PASS  a reply delivered in 3-byte pieces is reassembled
PASS  a reset connection says 'closed by QEMU' on every OS
PASS  a silent QEMU times out instead of hanging
```

The split-reply test sends a 30-byte reply in 3-byte pieces with a small pause between
them, which forces separate `recv` calls on any OS. The silence test holds the
connection open without answering and checks that the client gives up after its read
timeout (0.5 s here) rather than hanging. qemu-mcp gained this test when it was found
that its client let a raw `TimeoutError` escape instead of its own `QMPError`.

## Failure 1 (Windows): The Reset

On Linux, when QEMU exits, the client's next `recv()` returns `b""`, a clean EOF, and
the error says "connection closed by QEMU". On Windows the same event usually surfaces
as an exception: `ConnectionAbortedError: [WinError 10053]`, or `ConnectionResetError:
[WinError 10054]`. qemu-mcp's client caught generic `OSError` and reported "QMP connection
error: [WinError 10053] ...". It was correct, but different on Windows, and a test that
matched "closed by QEMU" failed there.

The fix maps the reset errors to the same message:

```python
except (ConnectionResetError, ConnectionAbortedError) as e:
    raise QMPError(f"QMP connection closed by QEMU ({e})") from None
```

The test reproduces the Windows behaviour on **every** OS. Setting `SO_LINGER` to
`(on, 0 seconds)` makes `close()` send a TCP RST instead of a FIN:

```python
conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
```

Now Linux and macOS raise `ConnectionResetError` too, and the test pins the fix
everywhere, not just on the one OS that happened to expose it.

## Failure 2 (Windows): Text Mode

A test faked a serial log with `path.write_text("boot ok\n")` and asserted the code read
back exactly `b"boot ok\n"`. On Windows, text mode translates `\n` into `\r\n`. The file
held 9 bytes, the comparison failed, and the code under test was fine:

```
PASS  text mode wrote 9 bytes on win32; binary always writes 8
```

The fix is `write_bytes(b"boot ok\n")`. QEMU writes the real log in binary, so the test
fixture should too. The general rule: **fixtures must be produced the way production
produces them.**

## Failure 3 (macOS): Two Sends, One recv

A serial test sent `"a"`, then `"b"`, then asserted that one `conn.recv(1024)` returned
`b"ab"`. Usually it does: both writes are on the receiving side by the time it reads. On
the macOS CI runner, the first `recv` sometimes returned just `b"a"`. TCP never promised
otherwise. The fix is to read until you have what you're waiting for:

```python
got = b""
while len(got) < 2:
    got += conn.recv(1024)
```

The demo prints whatever the first `recv` happened to return, then reassembles. On a
fast loopback the first read often already holds both bytes. The point is that the test
no longer depends on it.

## One Check Against Real QEMU

Fakes encode what you *believe* QEMU does. At least one test per behaviour should check
the belief against QEMU itself. The cross-version one is `quit`:

```
PASS  real QEMU 11.0 quit: replied, then closed; process exited
```

On Ubuntu 24.04's QEMU 8.2, the same check prints "closed without replying". It passes
there too, because what it asserts is the outcome that matters: the process exited.
Module 08's `stop()` is built on this result. A fake would have hidden the difference,
whichever version you modelled.

## What CI Runs

```yaml
- run: sudo apt-get update && sudo apt-get install -y qemu-system-x86 qemu-utils
- run: python verify.py
```

`verify.py` runs all nine modules against real QEMU on every push, and weekly so a new
QEMU or `mcp` release that breaks a module shows up on its own. Windows and macOS aren't
in the matrix; installing QEMU there isn't one line. This module's tests are the
compromise: each of those platforms' failure modes is reproduced deliberately on
whatever OS runs the suite.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
fake QMP server, real sockets:
  PASS  an event between command and reply is filed, not returned
  PASS  a reply delivered in 3-byte pieces is reassembled
  PASS  a reset connection says 'closed by QEMU' on every OS - QMP connection closed by QEMU ([WinError 10054] ...
  PASS  a silent QEMU times out instead of hanging
cross-platform lessons:
  PASS  text mode wrote 9 bytes on win32; binary always writes 8
  PASS  first recv() returned b'ab'; reading until 2 bytes gives b'ab'
real QEMU:
  PASS  real QEMU 11.0 quit: replied, then closed; process exited
OK
```

## Where to Go Next

- **Read qemu-mcp's source** alongside this course. It adds mouse input, `extra_args`
  conflict checks, `qemu_qmp` as an escape hatch, and screenshots as MCP images.
- **Fix the two gaps this course found.** `qemu_wait_serial` restarts from byte 0 on
  every call (Module 05), and `qemu_type` sends while it translates (Module 04).
- **Debug what you boot.** [gdbstub-mcp](https://github.com/0xmortuex/gdbstub-mcp) and
  [MCP Server — The Hard Way](https://github.com/0xmortuex/mcp-server-the-hard-way) pick up
  where a crashed kernel leaves off.
