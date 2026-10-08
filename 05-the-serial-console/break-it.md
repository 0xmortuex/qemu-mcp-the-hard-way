# Break It: The Serial Console

## Challenge 1: The Stale Match

Make `wait_serial` ignore `since` and read the whole file. Send `ping`, wait for
`serial got:`, send `pong`, and wait for `serial got:` again. How long does the second
wait take?

**Hint:** Zero seconds, because it matched the first reply. The test "passes" and
reports the wrong thing. Any tool that waits on a growing log must say *where* it
started waiting.

## Challenge 2: Connect Per Send

Rewrite `SerialConsole.send` to connect, send and close every time. The course kernel
won't notice. Now boot a Linux guest with `console=ttyS0` and a getty on it, log in
over the socket, and send two commands that way.

**Hint:** Watch for the session ending between commands. This is the hangup qemu-mcp
designs around. Note that its BACKLOG lists this as untested against a real getty. Doing
this challenge is how you'd find out.

## Challenge 3: Exact Line Endings

Change the demo's checks to match `b"serial got: ping\r\n"` exactly on the raw bytes.
Run it on Windows and on Linux.

**Hint:** It passes on one OS and fails on the other. Assertions on exact line endings
are tests of the platform, not your code. Normalise, or decide what you mean and test
only that.

## Challenge 4: No Logfile

Remove `logfile=` from the chardev and read guest output from the socket instead. Then
connect only *after* the kernel prints `ready`.

**Hint:** Socket chardevs don't buffer output for a client that isn't there. Everything
printed before you connected is gone, including boot messages and the first panic.
`logfile=` is the complete record; the socket is a live tap.

## Challenge 5: Binary Garbage

Make the kernel print bytes `0x80`–`0xFF` to COM1 right before `ready`. Run
`wait_serial` with `errors="strict"` instead of `"replace"`.

**Hint:** A `UnicodeDecodeError` replaces the timeout you were expecting, and hides the
marker that followed the garbage. Crashing kernels print exactly this sort of thing.
Decode leniently and match on what you care about.

→ [Next: Module 06](../06-snapshots/README.md)
