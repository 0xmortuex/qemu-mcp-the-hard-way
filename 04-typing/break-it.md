# Break It: Typing

## Challenge 1: No Delay

Set `delay=0` in `type_text` and type a 70-character line. Compare what the kernel
echoes with what you sent. Then try again while the guest is busy, for example during
the boot progress bar.

**Hint:** Dropped or reordered characters show up first on long strings and busy
guests. If everything arrives, add a busy loop to the kernel's main loop and watch the
8042's single-byte buffer overflow. Delays you never needed locally are what keep CI
green.

## Challenge 2: Type Before Ready

Remove `wait_for(log, b"ready")` and type immediately after QMP connects. What does the
kernel echo?

**Hint:** Keys sent while the kernel is still drawing its progress bar sit in the PS/2
controller, which holds one byte. Most are lost, and the first key the kernel reads is
whichever survived. This is Module 01's lesson again, from the keyboard's side.

## Challenge 3: A Different Layout

Change the kernel's `plain[]` table so `y` and `z` are swapped (a QWERTZ-ish layout) and
type `yes`. What arrives?

**Hint:** QKeyCodes are physical keys, and the guest maps them to characters. Your
translation tables assume what that mapping is. A tool can't detect the guest's layout,
so qemu-mcp documents the US assumption instead of guessing.

## Challenge 4: Stuck Modifier

Send `["shift"]` alone with `hold-time` 50 ms, then type `abc`. Now hand-craft QMP's
`input-send-event` to press shift and *never* release it, then type `abc`.

**Hint:** `send-key` always releases what it pressed, which is why chords use it.
Lower-level input events give you full control, and with it the ability to leave the
guest believing shift is held forever. Every typed letter then comes out uppercase.

## Challenge 5: Half-Typed Commands

Restore the old send-as-you-go loop and type `count 5é`. Then type `count 6` and press
Enter. What does the kernel print?

**Hint:** The first call leaves `count 5` in the guest's line buffer. The second call
appends to it, so the kernel receives `count 5count 6`. The agent sees an error from the
first call and a strange result from the second, with no way to connect them.
Validating first makes every call all or nothing.

→ [Next: Module 05](../05-the-serial-console/README.md)
