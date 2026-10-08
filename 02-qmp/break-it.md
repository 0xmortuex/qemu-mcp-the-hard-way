# Break It: QMP

## Challenge 1: Forget the Handshake

Comment out `self.command("qmp_capabilities")` and send `query-status` straight after
the greeting. What does QEMU say?

**Hint:** QEMU refuses every command until capabilities are negotiated, and says so in
an `error` reply. Your client turns that into a `QMPError`. Without the error handling
from this module, you'd have parsed the error object as a result.

## Challenge 2: Remove the Lock

Delete `with self.lock:` (dedent the body) and run the 8-thread test a few times. Then
raise it to 32 threads.

**Hint:** Mismatches appear as soon as two threads interleave a send and a read. The
`query-name` replies show up in `query-status` threads and vice versa. If you see zero,
add a `time.sleep(0.001)` between `sendall` and `_read()` to widen the window. Real
races only need a window once.

## Challenge 3: Return the First Message

Change `command()` to return the first message it reads, whatever it is. Run the
`stop`/`cont` sequence.

**Hint:** `query-status` right after `cont` can receive the `RESUME` event as its "reply".
The bug depends on timing, so it passes most runs and fails on a loaded machine. The
interleaving is defined behaviour in QMP, not a QEMU bug.

## Challenge 4: Split the Reply

Run the client against a server that sends each reply one byte at a time (Module 09's
fake server can do this). Then replace the buffered reader with
`json.loads(self.sock.recv(65536))`.

**Hint:** The naive version usually works against real QEMU on loopback, because
replies are small and arrive in one segment. It fails on large replies such as
`query-qmp-schema` and on slow links. Buffer and split on newlines; never assume one
`recv` is one message.

## Challenge 5: A Slow Command

Set `read_timeout=0.05` and run `human-monitor-command` with `info mtree`, a large
output. Does it time out? Then choose a timeout policy for an MCP tool: fixed, or
per-call?

**Hint:** qemu-mcp chose a per-VM read timeout set at boot (`qmp_read_timeout_s`). A
single global value is either too short for `savevm` on a large guest or too long to
notice a hung QEMU.

→ [Next: Module 03](../03-seeing-the-screen/README.md)
