# Module 05: The Serial Console

## The Concept

For a kernel developer, the serial port is the most honest output a machine has. It
works before the graphics driver, after the graphics driver crashes, and on hardware
with no screen at all. For an agent it's better than screenshots: text, exact, and
cheap to read.

Module 01 sent COM1 to a file with `-serial file:`. That gives you output, but there's
no way to send anything back. This module replaces it with a **socket chardev with a
logfile**, which gives you both. It also covers the three things that make serial
automation subtle: waiting on a moving log, keeping the connection open, and line
endings.

## One Chardev, Two Outlets

```python
"-chardev",
f"socket,id=serial0,host=127.0.0.1,port={port},server=on,wait=off,"
f"logfile={log},logappend=on",
"-serial", "chardev:serial0",
```

A *chardev* is QEMU's name for a host-side character device. This one is a TCP
server; `wait=off` again means boot without waiting for a client. `-serial
chardev:serial0` plugs COM1 into it. `logfile=` is what makes the design work: QEMU
writes **everything the guest prints** to that file, whether or not a client is
connected.

```
guest COM1 ──► chardev serial0 ──┬──► logfile   (always: the complete record)
                                 └──► TCP client (only while one is connected)
TCP client ──► chardev ──► guest COM1 input
```

So reading never depends on the socket. Boot output printed before anyone connected is
in the log:

```
boot output (from the logfile, no client connected): 'kernel: hello from COM1\nready\n> '
```

The socket is for writing, and for anyone who wants a live stream: the demo shows the
connected client receiving the guest's reply directly too.

One practical detail: QEMU parses chardev options as a comma-separated list, and a
Windows path with backslashes is a hazard there. The demo passes the path with forward
slashes, which Windows accepts.

## Keep the Connection Open

The obvious sender connects, writes and closes:

```python
def send(text):
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(text.encode())
```

qemu-mcp deliberately doesn't. A client disconnecting from a socket chardev is a
*close* event on that chardev, and qemu-mcp's design treats it the way a serial line
treats a dropped connection: as a **hangup**. A guest that watches for carrier, which
`getty` and login shells do, can end the session and kill the shell. Connect-per-send
would risk that after every single command.

`SerialConsole` connects lazily, keeps the socket, reconnects only if a write fails, and
holds a lock across all of it, so two concurrent senders can't interleave bytes on the
wire or race on the socket:

```python
with self.lock:
    if self.sock is None:
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
    try:
        self.sock.sendall(text.encode())
    except OSError:
        self.sock.close()
        self.sock = socket.create_connection(...)
        self.sock.sendall(text.encode())
```

The course kernel doesn't watch carrier, so the demo can't show a getty dying. qemu-mcp
lists that as untested against a real getty. What the demo does check is that every send
reuses the one connection.

## Waiting on a Moving Log

`wait_serial(marker)` from Module 01 has a flaw once you send more than one command.
After `ping`, the log contains `serial got: ping`. Send `pong` and wait for `serial
got:` again, and it matches the *old* reply instantly.

The fix is to wait on output written **after a byte offset**, and return the new offset
for the next wait:

```python
with open(log, "rb") as fh:
    fh.seek(since)
    new = fh.read()
if marker in normalize(new):
    return normalize(new), since + len(new)
```

The demo proves the stale reply is skipped: after sending `pong` from the new offset,
the text it waited on doesn't contain `ping`.

qemu-mcp's `qemu_wait_serial` reads the log incrementally *within* one call, so each
poll reads only new bytes. But every call used to start again from byte 0: wait twice for
the same marker and the second wait matched the first occurrence. Writing this module
found that. qemu-mcp fixed it (commit `1d00b93`) by keeping the offset itself, per VM,
instead of returning it to the agent: each wait searches only output after the previous
match, and `from_start=True` searches the whole log. That's the other design choice
from the `since` offset here. It keeps the tool's interface small, and the agent can't
pass back a stale offset.

## Line Endings You Can't Trust

The kernel sends `\r\n`, the terminal convention. Look at the bytes on disk on Windows:

```
raw bytes on disk end with: b'\r\nready\r\r\n> '
```

With QEMU 11 on Windows, the socket chardev's logfile had `\r\r\n` for most lines, while
`-serial file:` (Module 01) kept a clean `\r\n`. On Linux the logfile has exactly what the
guest sent. Whatever the cause, the lesson is the same: **don't match line endings**.
`normalize` drops every `\r` before comparing:

```python
def normalize(data: bytes) -> str:
    return data.replace(b"\r", b"").decode("utf-8", errors="replace")
```

The same lesson appears in Module 09 from the test side: a qemu-mcp test that wrote
its fake log with `write_text("boot ok\n")` failed only on Windows, because text mode
wrote `\r\n`.

`errors="replace"` matters too. A guest can emit any byte, especially while crashing,
and a decode error must not hide the panic message you were waiting for.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
boot output (from the logfile, no client connected): 'kernel: hello from COM1\nready\n> '
raw bytes on disk end with: b'\r\nready\r\r\n> '
sent 'ping' over the socket -> guest replied 'serial got: ping'
second wait saw only new output (stale 'ping' reply skipped)
one persistent connection for every send
the socket carries guest output too - it is a real terminal line
OK
```

The raw-bytes line differs on Linux, where it shows plain `\r\n`, and the module passes
on both because nothing depends on it.
