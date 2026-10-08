# Break It: Snapshots

## Challenge 1: Trust the QMP Status

Change `snapshot()` to call `hmp()` and ignore its output. Boot with `format=raw` on
the qcow2 disk and "save" a snapshot. Then try to load it.

**Hint:** The save reports success, and the load fails with "does not exist". Nothing
connects the two errors, and an agent would have no way to know its checkpoint never
existed. Any API that reports failure inside a successful reply needs its output
checked.

## Challenge 2: No Disk At All

Boot without any `-drive` and run `savevm s1`. What does QEMU say? Then add a raw-format
disk built with `qemu-img create -f raw`.

**Hint:** With no disk, there's nowhere to store the snapshot. With only raw disks, none
supports internal snapshots. qemu-mcp documents that `qemu_snapshot_save` needs a qcow2
`disk`. Good error text from QEMU only helps if your tool passes it through.

## Challenge 3: Let QEMU Probe

Drop `format=` from `-drive` entirely. Read the warning QEMU prints on stderr. Then look
up why QEMU added it.

**Hint:** Format probing on a raw image lets whatever data the guest wrote decide how
the host interprets the file. A guest that writes a qcow2 header with a backing file
pointing at a host path can make the next boot read that path. Always pass the format
you detected.

## Challenge 4: Snapshot the Wrong Thing

Save a snapshot, then send `count 9\r` and read the log. Load the snapshot and read the
log file again. Is `count=9` still in it?

**Hint:** Yes. The logfile is on the host and isn't part of the VM. Snapshots rewind the
guest, not your evidence. That's useful, because you keep the record of what happened,
but a tool comparing logs before and after a restore has to know the log only grows.

## Challenge 5: A Hostile Tag

Remove `TAG_RE` and save a snapshot named `a b`. Then run `info snapshots`. Now try a tag
containing a newline.

**Hint:** HMP's parser treats the space as an argument separator, so you get a snapshot
called `a`, or a syntax error, depending on the command. Restricting the alphabet is
less clever than escaping and much harder to get wrong.

→ [Next: Module 07](../07-tool-design/README.md)
