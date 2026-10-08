# Module 03: Seeing the Screen

## The Concept

An agent can't look at a monitor, but it can look at an image. QMP's `screendump`
command writes the guest's display to a file, even with `-display none`. Turn that into a
PNG and the agent can see the boot menu, the kernel panic, or the login prompt.

The second job is harder: knowing *when* to look. "Wait until the screen stops changing"
sounds simple. On this course's kernel, the obvious implementation never returns at all.
This module shows why, and how qemu-mcp-style tools should handle it.

## screendump

```python
qmp.command("screendump", filename=ppm)
```

QEMU writes a **binary PPM**, about the simplest image format there is:

```
P6\n          magic: binary RGB
720 400\n     width height (VGA text mode: 80x25 cells of 9x16 pixels)
255\n         max value per channel
RGBRGBRGB...  width x height x 3 bytes, row by row
```

`read_ppm` splits off the four header tokens and checks the pixel count adds up. A
truncated file is an error, not a short image. The file is deleted before each dump and
polled until it parses, rather than read the instant the reply arrives. That's cheap
insurance against reading a half-written file, and qemu-mcp does the same.

## PPM to PNG With No Dependencies

PPM is easy for QEMU and useless for everything else. MCP clients display PNG. You could
reach for Pillow (qemu-mcp does), but a minimal PNG is about twenty lines:

```python
def chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
       + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
```

A PNG is:

- an 8-byte signature
- a sequence of chunks, each made of a length, a type, the data, and a CRC-32 over the
  type and data
- `IHDR`: the image size, 8 bits per channel, colour type 2 (truecolour RGB)
- `IDAT`: the pixels, zlib-compressed, with each scanline prefixed by a *filter byte*
  (0 means "no filter")
- `IEND` to finish

A 720×400 text screen is 864 KB as PPM and about 1.3 KB as PNG, because text-mode
screens are mostly flat colour. That matters when the image goes into an agent's context.

## Waiting for the Screen to Settle

Boot screens are animated: progress bars, spinners, scrolling logs. Taking a screenshot
mid-animation and handing it to an agent wastes a turn. The idea behind qemu-mcp's
`qemu_wait_screen` is to poll screenshots until N in a row are identical.

The naive version compares hashes:

```python
same = same + 1 if hash(frame) == hash(prev) else 1
if same >= 3:
    return frame
```

Run it on the course kernel and it **never returns**. The demo shows why. After boot,
with nothing happening, two frames 0.35 s apart still differ:

```
settled screen, two frames 0.35s apart: 18 px differ (the blinking cursor)
```

Eighteen pixels: a 9×2 block, the VGA text-mode cursor's underline, blinking at about
2 Hz. QEMU renders the blink into `screendump`, so a byte-identical comparison can't
succeed on any text console with a visible cursor. That covers BIOS screens, bootloaders,
Linux consoles and most hobby kernels.

## Change Magnitude, Not Change

The fix is to measure *how much* changed, and ignore changes too small to mean anything:

```python
CURSOR_NOISE_PX = 64

unchanged = prev is not None and changed_pixels(prev, px) <= CURSOR_NOISE_PX
```

Compare the numbers from the demo:

```
two frames 0.5s apart during boot: 438 px changed
settled screen, two frames 0.35s apart: 18 px differ
```

A progress-bar tick, a character, any real update touches far more than a cursor. One
glyph alone is 9×16 = 144 pixels. A threshold of 64 sits safely between the two. It's a
heuristic, and the README says so, but it's a heuristic with measurements behind it.
Another approach: compare frames two polls apart, so a blink, which returns to the same
state, cancels out. That fails when the poll interval beats against the blink rate.
Break-it challenge 3 explores this.

A side effect: frames can't be compared by hash any more, so `screenshot()` returns raw
pixels. `changed_pixels` short-circuits on `a == b`, so identical frames cost one memory
comparison, not a pixel loop.

## What "Settled" Promises

A settled screen is evidence, not proof. The demo checks it against a second source,
the serial log:

```
screen settled after 3 polls; serial says ready: True
```

A kernel that hangs mid-boot also has a "settled" screen. That's why qemu-mcp offers
both `qemu_wait_screen` and `qemu_wait_serial`: the screen tells you nothing is
moving, and the serial marker tells you the guest got where it was going. For VGA-only
guests with no serial output, the screen is all you have, which is why getting the
cursor right matters.

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
two frames 0.5s apart during boot: 438 px changed (kernel ready yet: False)
settled screen, two frames 0.35s apart: 18 px differ (the blinking cursor)
screen settled after 3 polls; serial says ready: True
PNG: 1269 bytes, 720x400 (VGA text mode is 720x400)
PNG decodes back to the same number of pixels
OK
```

The last check round-trips the PNG: it decompresses the `IDAT` chunk and checks it holds
exactly `height × (1 + width × 3)` bytes. That's the filter byte plus RGB for every row.
