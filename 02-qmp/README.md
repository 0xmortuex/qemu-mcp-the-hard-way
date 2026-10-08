# Module 02: QMP

## The Concept

The QEMU Machine Protocol (QMP) is how a program controls a running VM: query its
state, pause it, take a screenshot, press keys, quit. Every later module is built on
it. Getting the client right is less about the happy path than about three properties
of the protocol that are easy to miss:

- QEMU **talks first**, and nothing works until you answer.
- **Events arrive whenever they like**, including between your command and its reply.
- There are **no request IDs**, so two threads on one socket can steal each other's
  replies.

`main.py` builds the client from a socket and `json`, then proves each property
against real QEMU.

## The Wire

QMP is newline-delimited JSON over a socket. Enable it with
`-qmp tcp:127.0.0.1:PORT,server=on,wait=off`:

- `server=on` makes QEMU listen.
- `wait=off` boots the guest without waiting for you to connect.

A session looks like this:

```
QEMU → {"QMP": {"version": {...}, "capabilities": ["oob"]}}     greeting
you  → {"execute": "qmp_capabilities"}                           negotiate
QEMU → {"return": {}}
you  → {"execute": "query-status"}
QEMU → {"event": "RESUME", "timestamp": {...}}                   ← not your reply!
QEMU → {"return": {"status": "running", "running": true}}
```

Until `qmp_capabilities` is sent, QEMU answers every command with an error saying it
expects capabilities negotiation first. The client reads the greeting and sends
`qmp_capabilities` in its constructor, so no caller ever sees a half-open session.

## Connecting: Retry, Don't One-Shot

```python
while time.monotonic() < deadline:
    try:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        break
    except OSError as e:
        last = e
        time.sleep(0.1)
```

QEMU opens the QMP port shortly after it starts, and "shortly" varies. On a cold
Windows machine, antivirus can scan the QEMU binary for several seconds on its first
launch. A single connect attempt races the process startup and loses sometimes, which
is the worst kind of bug to diagnose. The connect timeout is a parameter for that
reason, and qemu-mcp exposes it as `qmp_connect_timeout_s` on `qemu_boot`.

## Reading: Lines, Not Packets

```python
while b"\n" not in self.buf:
    chunk = self.sock.recv(65536)
    ...
    self.buf += chunk
line, self.buf = self.buf.split(b"\n", 1)
```

One `recv()` can return half a message, or a message and a half. TCP is a byte stream:
message boundaries are something you parse out of it. The buffer keeps whatever came
after the newline for the next read.

Each failure becomes the same exception type with a clear message:

- an empty `recv` is "connection closed by QEMU"
- a timeout is "QEMU stopped responding"
- on Windows, a connection reset is also "closed by QEMU" (Module 09 covers why)

## Events Between Replies

`command()` sends a request, then reads until it sees `return` or `error`. Anything
with an `event` key is filed away and skipped:

```python
resp = self._read()
if "event" in resp:
    self.events.append(resp)   # keep it; it's not our reply
    continue
```

Skip this and the first `STOP`, `RESUME` or `SHUTDOWN` event gets returned as the answer
to whatever you asked. The demo triggers this on purpose: `stop` and `cont` each emit an
event, and the client must still return the right replies:

```
events seen while running commands: ['STOP', 'RESUME']
```

Errors need the same care. A bad command doesn't raise anything on the wire; it returns
`{"error": {"class": ..., "desc": ...}}`. The client turns that into a `QMPError`
carrying the description, so callers can't mistake a failure for an empty result.

## The Lock

QMP has no request IDs. Reply N belongs to command N only because they're in order.
Now let two threads share the client:

```
thread A: send query-status
thread B: send query-name
thread A: read → {"return": {"name": ...}}     ← B's reply
thread B: read → {"return": {"running": ...}}  ← A's reply
```

Both threads get a well-formed answer to the wrong question. Nothing crashes, so the
bug surfaces as nonsense values somewhere else. The fix is one lock, held for the
**whole** round trip:

```python
with self.lock:
    self.sock.sendall(json.dumps(msg).encode() + b"\n")
    while True:
        resp = self._read()
        ...
```

Holding it only around the send, or only around the read, doesn't help. The pairing
of the two is what has to be atomic.

An MCP server needs this lock even if your code never starts a thread. The MCP SDK
runs synchronous tools in worker threads, so `qemu_wait_screen` polling screenshots
while the agent calls `qemu_type` is exactly this race. The demo runs 200 commands from
8 threads and checks every reply has the right shape:

```
200 concurrent commands, mismatched replies: 0
```

## Timeouts

Every read has a timeout (`settimeout(read_timeout)`). A wedged QEMU, or a command that
legitimately takes a long time, otherwise blocks the tool call forever, and with it the
agent. qemu-mcp makes it configurable (`qmp_read_timeout_s`) because a `savevm` of a
large VM really can take longer than 15 seconds.

The timeout raises a `QMPError`, not a raw `TimeoutError`. A caller catching `QMPError`
around every QMP call (as qemu-mcp's tools do) must not be surprised by a different
exception type for the same kind of failure. qemu-mcp's first client let
`TimeoutError` and `BrokenPipeError` escape raw. That was fixed, and the fix came with
tests that hold a fake server's connection open without replying (Module 09).

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
greeting: QEMU 11.0.50, capabilities ['oob']
query-status -> {'status': 'running', 'running': True}
bad command -> no-such-command: The command no-such-command has not been found
events seen while running commands: ['STOP', 'RESUME']
200 concurrent commands, mismatched replies: 0
no QEMU on port -> could not connect to QMP on 127.0.0.1:58022: ... after 2.1s
OK
```

The last line shows the connect timeout. With nothing listening, the client gives up
after its deadline with a message naming the port, instead of hanging or raising a bare
`ConnectionRefusedError`.
