# Break It: Seeing the Screen

## Challenge 1: The Hash Wait

Replace `wait_screen`'s comparison with a SHA-256 of the pixels, as described in the
README. Run it with a 30-second timeout. Then run it after the kernel disables the
cursor (write `0x20` to CRT controller register `0x0A`: `outb(0x3D4, 0x0A);
outb(0x3D5, 0x20)` in `kmain`).

**Hint:** With the cursor on, it times out every time. With the cursor off, it settles
after three polls. Same code, same kernel, one register. That's why a tool can't assume
the guest will cooperate.

## Challenge 2: Tune the Threshold

Set `CURSOR_NOISE_PX = 0`, then `200`. Find the smallest real change the kernel makes,
for example by typing one character and measuring it with `changed_pixels`.

**Hint:** One glyph is a 9×16 cell, but a narrow character like `.` changes only a few
dozen pixels. Too high a threshold calls a screen "settled" while a dot-by-dot progress
indicator is still moving. Measure, don't guess, and write down why the number is what
it is.

## Challenge 3: Compare Every Other Frame

Instead of a threshold, compare each frame with the one two polls back, so a blink
cancels out. Try poll intervals of 0.25 s, 0.3 s and 0.5 s.

**Hint:** A ~2 Hz blink has a period of about 0.5 s. At 0.25 s, frames two apart are one
period apart and the blink cancels. At other intervals the phase drifts and it doesn't.
The scheme works only by accident of timing, which is why this module measures magnitude
instead.

## Challenge 4: A Truncated PPM

Make `read_ppm` read only the first 100 KB of the file. What happens, and what *should*
happen?

**Hint:** Without the length check, you'd build a PNG with a short last scanline, and
most decoders would reject it or show garbage. The check turns "corrupt image" into a
clear error, and the retry loop in `screenshot()` turns "not finished writing" into a
short wait.

## Challenge 5: Graphics Mode

Boot something that sets a framebuffer mode, such as a Linux ISO or a GRUB menu with a
background image. Does 64 pixels still separate real changes from noise? What size is
the PPM?

**Hint:** In graphics modes there's no hardware cursor, but there may be a software one,
or an animated spinner. Resolution changes mid-boot also change the frame size, and
`changed_pixels` treats a size change as "everything changed". That's correct: a mode
switch is not settled.

→ [Next: Module 04](../04-typing/README.md)
