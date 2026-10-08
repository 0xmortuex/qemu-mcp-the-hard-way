# Module 08: Lifecycle

## The Concept

An agent session doesn't boot one VM and exit. It boots one, boots a second to
compare, abandons the first mid-task, and gets interrupted. Meanwhile one guest powers
itself off and another fails to boot at all. The MCP server outlives all of it, so
everything it creates has to be accounted for:

- every QEMU process
- every socket
- every temp directory

This module builds the registry and the stop paths, and fixes the leak qemu-mcp shipped:
**every boot leaked a temp directory, forever.**

## Named VMs

```python
VMS: dict[str, VM] = {}

def validate_name(name):
    if not name or any(c in name for c in "/\\") or name in (".", ".."):
        raise ValueError(f"invalid VM name {name!r}: must be non-empty with no path separators")
```

The agent picks a name at boot and uses it in every later call, so two VMs ("old
kernel" and "new kernel") can run side by side. The name also goes into the work
directory (`tempfile.mkdtemp(prefix=f"...{name}-")`) and QEMU's `-name`, which is why it
is validated **first**. qemu-mcp once let `a/b` through. `mkdtemp` then failed with
`FileNotFoundError: No such file or directory: '/tmp/qemu-mcp-a/b-XXXX'`, an error
about a path nobody asked for. Duplicates are refused too: "already running - stop it
first" beats silently orphaning the old VM.

## Three Ways a VM Ends

```
1. you stop it          quit ──► QEMU exits        (or kill, if it won't)
2. the guest stops it   poweroff ──► QEMU exits on its own
3. it never starts      QEMU exits immediately, QMP never answers
```

Every path must end the same way: process reaped, socket closed, work directory gone.

### 1. Stopping: quit, then kill

```python
try:
    vm.qmp.command("quit")
except QMPError:
    pass
try:
    vm.proc.wait(timeout=graceful_timeout)
    how = "quit"
except subprocess.TimeoutExpired:
    vm.proc.kill()
    vm.proc.wait()
```

`quit` lets QEMU shut down cleanly: flush disk images, close files. Two details:

- **The reply is optional.** QEMU 11 answers `quit` and then closes the socket. QEMU
  8.2 (Ubuntu 24.04) closes it first, so the client sees "connection closed". A stop
  path that treats that as failure breaks on half the QEMU versions in use. What counts
  is the process exiting.
- **Kill is the fallback, not the plan.** A wedged QEMU must still go away, but kill
  skips the disk flush. It also logs which path was taken, so a test can assert the
  graceful one.

### 2. The Guest Powers Off

The course kernel's `poweroff` writes the ACPI sleep command, and QEMU exits with code
0. Nobody called `stop()`, but the registry still holds a dead VM, a closed socket and
a work directory. `reap_dead()` sweeps them:

```python
dead = [n for n, vm in VMS.items() if not vm.running]
```

qemu-mcp runs that sweep at the start of `qemu_list` and `qemu_boot`, so a VM the guest
shut down disappears cleanly instead of failing confusingly on its next call.

You might expect to read QMP's `SHUTDOWN` event to learn *why* it ended. Don't count on
it:

```
guest typed poweroff -> QEMU exited with 0; reaped ['beta']; events readable after exit: none
```

QEMU sends the event on its way out, but once the process has exited and the connection
has dropped, there was nothing left to read here. The exit code is the dependable
signal. When you do need the event, boot with `-no-shutdown`. QEMU then pauses instead
of exiting, and the event arrives on a socket that's still open:

```
with -no-shutdown: events ['SHUTDOWN', 'STOP'], status 'shutdown', process still alive: True
```

### 3. The Boot That Fails

Point `boot()` at a missing kernel and QEMU exits at once. The QMP connect loop notices,
because it checks `proc.poll()` on every retry, as Module 01 recommends. Then cleanup
runs:

```python
except QMPError as e:
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    ... read qemu.stderr ...
    shutil.rmtree(workdir, ignore_errors=True)
    raise RuntimeError(f"boot of {name!r} failed: {e}. QEMU said: {why[:200]}")
```

- **`proc.wait()` before `rmtree`.** On Windows a file that a process still holds open
  can't be deleted. Remove the directory before the process is reaped and `rmtree`
  quietly leaves it behind.
- **QEMU's own words go into the error**: "could not open kernel file ...". That's the
  sentence the agent needs.

## The Leak

qemu-mcp's original `stop()` killed QEMU, removed the VM from its registry, and
**never deleted the work directory**. Each boot and stop left behind a temp directory
holding the serial log, screendumps and snapshot scratch files. A long agent session
leaves hundreds. The failed-boot paths leaked too. Each fix was one `rmtree` line, plus
a test that boots, stops and asserts the directory is gone. This demo does the same for
all three exits, including counting the temp entries before and after a failed boot.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
two named VMs running: ['alpha', 'beta'] (pids 19884, 20492)
duplicate and path-like names rejected before touching the filesystem
stop('alpha') -> quit; process exit code 0; workdir removed: True
guest typed poweroff -> QEMU exited with 0; reaped ['beta']; events readable after exit: none
with -no-shutdown: events ['SHUTDOWN', 'STOP'], status 'shutdown', process still alive: True
failed boot -> boot of 'broken' failed: QEMU exited immediately (code 1). QEMU said: qemu: could not open kernel file '...missing.elf'
no workdir leaked by the failed boot
OK
```

The `finally` block stops anything still registered, even if an assert fails halfway.
A failing demo shouldn't leak QEMU processes onto your machine either.
