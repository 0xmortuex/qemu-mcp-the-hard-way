# Module 06: Snapshots

## The Concept

A snapshot saves the whole machine at once: CPU registers, RAM and device state, disk
contents. Load it and the guest continues from exactly that instant. For an agent this
is a superpower. It can boot once, snapshot at the login prompt, and then try ten risky
things, restoring after each one. Testing an install script no longer costs a reboot.

Snapshots are also where qemu-mcp shipped its quietest bug: one that made every
snapshot fail without saying why. This module reproduces it, then builds snapshots that
work and fail loudly.

## Where Snapshots Live

Internal snapshots (`savevm`) are stored *inside* a disk image, and only one format can
hold them: **qcow2**. The VM therefore needs at least one writable qcow2 disk, even if
the guest boots with `-kernel` and never reads the disk at all:

```python
subprocess.run([qemu_img, "create", "-q", "-f", "qcow2", disk, "16M"], check=True)
...
"-drive", f"file={disk},format={fmt},if=ide",
```

A 16 MB empty qcow2 file takes a few hundred kilobytes on disk. The snapshot's RAM image
is added to it when you save.

## The Bug: qcow2 Opened as Raw

qemu-mcp's `qemu_boot` used to build every disk argument as:

```python
"-drive", f"file={disk},format=raw"
```

Pass it a qcow2 file and QEMU does what it's told: it opens the file as raw bytes. The
guest sees qcow2's metadata as the first sectors of its disk, which a `-kernel` guest
never notices. The VM boots, runs, takes screenshots, and accepts typing. Then:

```
format=raw on a qcow2 file -> savevm returned:
  "Error: Device 'ide0-hd0' is writable but does not support snapshots"
```

The disk *is* qcow2. QEMU just wasn't told so. The fix is to read the first four bytes:

```python
QCOW2_MAGIC = b"QFI\xfb"

def disk_format(path):
    with open(path, "rb") as fh:
        return "qcow2" if fh.read(4) == QCOW2_MAGIC else "raw"
```

Why not just omit `format=` and let QEMU probe? QEMU will, with a warning, but probing a
raw image is a known security risk: a guest could write a qcow2 header into its own raw
disk and make the next boot read host files. Detecting the format yourself and always
passing it explicitly is the safe pattern.

## HMP Through QMP

Newer QEMU has a QMP `snapshot-save` command, but it runs as a background job you have
to poll. The long-standing one-shot interface is the human monitor's (HMP) `savevm`,
and it's what qemu-mcp uses. QMP can reach HMP:

```python
qmp.command("human-monitor-command", **{"command-line": "savevm half-typed"})
```

Here's the trap. **`human-monitor-command` succeeds at the QMP level even when the HMP
command fails.** The failure arrives as an ordinary return value, a string of error
text:

```python
out = hmp(qmp, "savevm s1").strip()
# "Error: Device 'ide0-hd0' is writable but does not support snapshots"
```

A client that only checks for a QMP `error` reports this as success. For
`savevm`/`loadvm`/`delvm`, empty output means it worked, and any output is a failure:

```python
out = hmp(qmp, f"{verb} {tag}").strip()
if out:
    raise RuntimeError(f"{verb} {tag} failed: {out}")
```

`info snapshots` is the exception: its output is the point, so it's returned as data.

## Don't Escape, Restrict

The snapshot tag is spliced into a command line, and HMP splits arguments on spaces. A
tag like `s1 extra`, or `s1` followed by a newline and `quit`, is at best confusing and
at worst a second command. Rather than invent quoting rules for a human-oriented parser,
restrict tags to a safe alphabet:

```python
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
```

The demo checks the bad tags are rejected before anything is sent.

## Proving It Restores State

A snapshot test that only checks "savevm returned nothing" proves very little. The demo
shows guest state actually rewinding, using something the guest can't fake. Here's how
the course kernel's serial line buffer travels through a snapshot:

```
send "count "             line buffer: "count "        (no Enter yet)
savevm half-typed         ── snapshot taken here ──
send "7\r"                kernel prints count=7         buffer cleared
loadvm half-typed         buffer is "count " again
send "8\r"                kernel prints count=8         ← only possible if restored
```

`count=8` can only appear if the guest's RAM, and so its half-typed line, came back
from the snapshot. The host-side socket survives the restore. Only the guest rewinds.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
created disk.qcow2: first bytes b'QFI\xfb' -> detected qcow2
format=raw on a qcow2 file -> savevm returned: "Error: Device 'ide0-hd0' is writable but does not support snapshots"
snapshot() raises: savevm s1 failed: Error: Device 'ide0-hd0' is writable but does not support snap...
saved snapshot 'half-typed' with 'count ' in the guest's line buffer
finished the line with '7' -> count=7
loadvm, then typed '8' -> count=8: the guest's half-typed 'count ' came back
info snapshots:
  List of snapshots present on all disks:
  ID      TAG               VM_SIZE                DATE        VM_CLOCK     ICOUNT
  --      half-typed       1.31 MiB 2026-10-08 23:35:19  0000:00:03.002         --
missing tag -> loadvm nope failed: Error: Snapshot 'nope' does not exist in one or more devices
OK
```

The snapshot of a 64 MB guest is only 1.3 MB, because most of that RAM was never
touched. Error wording can differ between QEMU versions, which is why the check is "any
output", not a match on specific text.
