# Break It: Testing

## Challenge 1: Mock It Instead

Rewrite `test_reply_split_across_reads` with `unittest.mock`, patching `socket.recv` to
return the whole reply at once. Now break the client's buffering (parse each `recv` as
one message). Does the mocked test notice?

**Hint:** No: the mock never splits anything, so the broken client passes. The real
socket test fails at once. A test can only catch failures its fake is able to produce.

## Challenge 2: Undo the Reset Mapping

Remove the `except (ConnectionResetError, ConnectionAbortedError)` clause from the
client and run the reset test on Linux.

**Hint:** With `SO_LINGER(1, 0)` the server sends RST, and Linux raises
`ConnectionResetError` too. The test fails on your machine instead of only on someone
else's Windows box. Reproducing a platform's behaviour beats waiting for that platform's
CI.

## Challenge 3: Make the macOS Bug Happen Locally

Restore the single `recv` assertion. Add `time.sleep(0.05)` between the two sends, and
read *immediately* after the first send arrives (use `select` to wait for readability,
then `recv`).

**Hint:** Once the timing is forced, the "macOS-only" bug fails on every OS. Flaky
tests usually depend on timing you never forced. Make the bad timing deterministic in
the test, then fix the code.

## Challenge 4: A Fake That Lies

Change the fake server to reply to `quit` and keep the socket open. Write a test that
`stop()` returns `"quit"`. Then run Module 08 against real QEMU 8.2.

**Hint:** The fake encodes one QEMU version's behaviour. Your stop path, designed
against the fake, may wait forever for a close that real QEMU already did, or never
does. Keep one test per behaviour pointed at the real thing.

## Challenge 5: Port Races

Replace `bind(("127.0.0.1", 0))` with a fixed port, 4444. Run two copies of the suite
at once.

**Hint:** Both bind 4444, so one fails with "address already in use". Port 0 asks the OS
for a free port atomically. Even `free_port()` in the earlier modules, which binds,
reads the port and closes the socket, can race with another process grabbing it in
between. Rare, but real under parallel CI.
