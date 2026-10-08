# Break It: Launching QEMU

## Challenge 1: The Fixed Sleep

Replace `wait_serial` with `time.sleep(1)`, then check the log for `kernel: hello`. Run
it ten times. Now run it while another program pins every CPU core.

**Hint:** Under load, QEMU's TCG emulation slows down with everything else, and the
kernel's progress bar takes longer. A sleep that is safe on an idle machine is a coin
flip on a CI runner. If you ever need a sleep, it belongs inside a polling loop, not
instead of one.

## Challenge 2: `-nographic`

Swap `-display none` for `-nographic` and drop the `-serial` flag. Look at what lands
on the subprocess's stdout, and what happens if you write to its stdin.

**Hint:** `-nographic` multiplexes the serial port and the QEMU monitor onto stdio,
unless you redirect them yourself. Guest output is now mixed with monitor output, and
the monitor reads your stdin, so stray input can become QEMU commands.
`-display none` changes only the window.

## Challenge 3: The Windows Installer

On Windows, remove `C:\Program Files\qemu` from `PATH` (it usually isn't there anyway)
and delete `windows_search_dirs()`. What does a user with a fresh QEMU install see?
Then put QEMU somewhere unusual and set `QEMU_DIR`.

**Hint:** "QEMU not found", with QEMU installed, is the most common first-run failure
for tools that only call `which`. The search list is cheap. The bug report it prevents
is not.

## Challenge 4: The Kernel That Never Says Ready

Point `--kernel` at a file that isn't a kernel, such as `main.py`. How long until you
know? Then remove the `proc.poll()` check from `wait_serial` and try again.

**Hint:** QEMU refuses a non-multiboot file and exits with an error on stderr, almost
instantly. Without the `poll()` check, you find out only when the full timeout expires,
and the error you report ("marker never appeared") is wrong about the cause.

## Challenge 5: A Path With a Comma

Make the work directory `C:\temp\vm,1` or `/tmp/vm,1` and launch. Then try `-serial
file:` with that path.

**Hint:** Many QEMU option strings are comma-separated `key=value` lists, so a comma in
a path ends the value early. QEMU's escape is a doubled comma. qemu-mcp sidesteps the
problem by generating its own temp directory names, and Module 05 hits the same issue
with chardev paths.

→ [Next: Module 02](../02-qmp/README.md)
