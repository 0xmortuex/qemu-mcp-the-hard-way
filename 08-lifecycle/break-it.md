# Break It: Lifecycle

## Challenge 1: Count the Leaks

Delete the `shutil.rmtree` line in `stop()`. Boot and stop a VM 50 times in a loop,
then count the `qemu-hard-way-08-*` directories in your temp folder.

**Hint:** Fifty directories, each holding a serial log. An MCP server runs for hours, and
an agent boots freely. qemu-mcp's fix was one line, but finding it needed a test that
asserts the directory is gone, not just that the process exited.

## Challenge 2: Strict quit

Make `stop()` raise if `quit` doesn't get a reply. Run it against QEMU 11 locally, and
in Ubuntu 24.04's QEMU 8.2 (CI, or a container).

**Hint:** It passes on one and fails on the other, for the same correct shutdown. When a
protocol doesn't guarantee a reply, judge the outcome, which here means the process
exiting, not the acknowledgement.

## Challenge 3: rmtree Before wait

In the failed-boot path, swap the order: `rmtree` first, then `proc.wait()`. Run it on
Windows with a kernel path that makes QEMU fail slowly (for example, a huge file it has
to read first).

**Hint:** On Windows, QEMU's open stderr handle keeps `qemu.stderr` undeletable, and
`ignore_errors=True` hides the failure. The directory survives, and nothing tells you.
Reap the process, then clean up.

## Challenge 4: The Zombie Registry

Remove `reap_dead()`. Let the guest power off, then call `stop("beta")`. What happens?
Then call `boot("beta", ...)`.

**Hint:** `stop` sends `quit` to a dead socket, and `boot` refuses the name because it's
"already running". The registry says one thing and the process table another. Sweep
dead VMs before any call that reads the registry.

## Challenge 5: Kill Without Asking

Replace `quit` with `proc.kill()` everywhere. Attach a qcow2 disk, write to it from
the guest (or save a snapshot), then kill QEMU mid-write. Run `qemu-img check` on the
image.

**Hint:** A graceful quit flushes and closes the image. A kill can leave it needing
repair, or leave leaked clusters. That's why kill is the fallback after a timeout, not
the default.

→ [Next: Module 09](../09-testing-it-for-real/README.md)
