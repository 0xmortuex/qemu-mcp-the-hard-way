# Module 01: Launching QEMU Headless

## The Concept

Before an agent can do anything with a VM, three things have to work:

1. **Find the binary.** That means `qemu-system-i386`, `qemu-system-x86_64` or
   `qemu-system-aarch64`, wherever this machine keeps it.
2. **Start it with no window.** The agent has no screen to show one on.
3. **Know when the guest has booted.** You need to know this before you type, take a
   screenshot, or decide it failed.

All three sound trivial, and all three have bitten qemu-mcp. `main.py` does each of them
against the course kernel and checks the result.

## Finding the Binary

On Linux and macOS, `shutil.which("qemu-system-i386")` is the whole story: package
managers put QEMU on `PATH`.

On Windows it is not. The official installer drops everything into
`C:\Program Files\qemu` and does **not** add it to `PATH`. A tool that only calls `which`
works on the author's Linux box and fails on every fresh Windows machine:

```python
exe = f"qemu-system-{arch}"
hit = shutil.which(exe)
if hit:
    return hit
if sys.platform == "win32":
    for d in windows_search_dirs():
        cand = os.path.join(d, exe + ".exe")
        if os.path.isfile(cand):
            return cand
```

`windows_search_dirs()` checks three places:

- `$QEMU_DIR`, an escape hatch for unusual installs
- both Program Files directories
- one level of subdirectories under them, because some installs nest a versioned folder
  like `qemu\qemu-8.2.0`

Notice the arch check at the top of `find_qemu`. The arch is pasted into a file name,
so `"../../evil"` must be rejected before it gets anywhere near the filesystem.

## Errors That Say What Exists

When the binary isn't found, a bare "not found" is a dead end for an agent. It asked
for `x86_64`. Is QEMU missing entirely, or is only `i386` installed? qemu-mcp's error
lists everything it can see:

```
qemu-system-nosucharch not found. Install QEMU and put it on PATH
(or set QEMU_DIR on Windows). Found on this system: aarch64, alpha, arm, ..., i386, ...
```

Now the next call can be right. `installed_arches()` scans the same directories for
`qemu-system-*`, strips `.exe`, and skips the `...w.exe` twins. Those are the windowless
GUI-subsystem builds QEMU ships on Windows, not separate architectures.

qemu-mcp's first version of this check had a bug that only showed up in CI on Windows.
`shutil.which()` returns the file name with the case `PATHEXT` uses, `.EXE`, while
the test had created `.exe`. On Windows those are the same file, but `==` disagreed.
The fix is `os.path.normcase()` on both sides. Module 09 covers how tests catch this
kind of thing.

## Launching Headless

```python
cmd = [
    qemu,
    "-display", "none",        # headless: no SDL/GTK window
    "-kernel", kernel,         # multiboot kernel, no disk needed
    "-m", "64",
    "-serial", f"file:{serial_log}",
    "-no-reboot",              # a triple fault exits instead of looping
]
```

- **`-display none`, not `-nographic`.** `-nographic` also redirects the serial port and
  monitor to stdio, and a subprocess's stdio is the last place you want them.
  `-display none` removes the window and changes nothing else. The VGA device is still
  there, so Module 03 can still take screenshots.
- **`-kernel`** boots a multiboot ELF directly: no disk image, no bootloader. QEMU's
  firmware loads it and jumps to `_start`.
- **`-serial file:`** is the simplest way to see what the guest prints: COM1 goes
  straight into a file. Module 05 replaces it with something you can also write to.
- **`-no-reboot`** turns a guest reset into a QEMU exit. Without it, a kernel that
  triple-faults reboots, crashes and reboots forever, and looks "running" the whole time.

QEMU's stderr goes to a file in the VM's work directory, not to `DEVNULL`. When a boot
fails, for example because the kernel file is missing or a flag is wrong, QEMU explains
why on stderr, and Module 08 puts that text into the error the agent sees.

## Knowing When It Booted

QEMU starting tells you nothing about the guest. The process is up within milliseconds,
while the kernel takes seconds to reach its keyboard loop. Anything sent in between is
lost.

A fixed sleep is the classic mistake. Two seconds is too long on a fast machine and too
short on a CI runner under load. Instead, wait for something the guest says:

```python
while time.monotonic() - start < timeout:
    if proc.poll() is not None:
        raise RuntimeError(f"QEMU exited with code {proc.returncode} before {marker!r}")
    try:
        with open(log, "rb") as fh:
            if marker in fh.read():
                return time.monotonic() - start
    except FileNotFoundError:
        pass  # QEMU creates the file a moment after starting
```

Three details matter:

- **Check `proc.poll()` every iteration.** If QEMU died, waiting the full timeout just
  hides the real error.
- **Tolerate `FileNotFoundError`.** The log doesn't exist until QEMU creates it.
- **Read bytes, not text.** Serial output is whatever the guest sends, not necessarily
  valid UTF-8 or your platform's line endings (Module 05).

The course kernel prints `ready` once its main loop is running. On this machine that
took about 3 seconds, most of it the deliberately slow boot progress bar that Module 03
needs.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
found C:\Program Files\qemu\qemu-system-i386.exe
QEMU emulator version 11.0.50
wrong arch -> qemu-system-nosucharch not found. ... Found on this system: ...
guest printed 'ready' after 2.9s
serial log so far: b'kernel: hello from COM1\r\nready\r\n> '
OK
```

When it finishes it terminates QEMU, waits for the process, and deletes the work
directory. The assert at the end checks the directory is really gone. Module 08 explains
why that last line matters.
