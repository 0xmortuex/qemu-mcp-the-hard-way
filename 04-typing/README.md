# Module 04: Typing

## The Concept

To type into a guest, you press *keys*, not characters. QMP's `send-key` takes a list
of **QKeyCodes**: names of physical keys like `a`, `shift`, `ret` and `bracket_left`,
pressed together and then released. Typing `Hello!` means six chords: `shift+h`, `e`,
`l`, `l`, `o`, `shift+1`.

This module builds the translation from characters to qcodes, chords like `ctrl-alt-f2`,
and a `type_text` that can't leave half a command in the guest. Every piece is checked
against what the course kernel echoes back.

## Characters to Keys

```python
def char_to_keys(ch: str) -> list[str]:
    if len(ch) != 1:
        raise ValueError(f"expected one character, got {ch!r}")
    if ch.isascii() and (ch.islower() or ch.isdigit()):
        return [ch]
    if ch.isascii() and ch.isupper():
        return ["shift", ch.lower()]
    if ch in PLAIN:
        return [PLAIN[ch]]
    if ch in SHIFTED:
        return ["shift", SHIFTED[ch]]
    raise ValueError(f"cannot type {ch!r}: no key for it on a US keyboard")
```

The structure follows the keyboard:

- **Letters and digits** are their own qcode names.
- **Uppercase** is shift plus the lowercase key.
- **Unshifted punctuation** has a name: `-` is `minus`, `[` is `bracket_left`, space is
  `spc`, newline is `ret`.
- **Shifted punctuation** is shift plus the key it lives on: `!` is `shift+1`, `{` is
  `shift+bracket_left`, `"` is `shift+apostrophe`.

The tables encode a **US layout**. That isn't a simplification you can patch later.
The guest's keyboard driver decides what the keys mean. The course kernel's scancode
table is US, like most hobby kernels and BIOS setup screens. A guest set to a German
layout would type `z` when you send `y`.

The first line, `len(ch) != 1`, fixes a real qemu-mcp bug. `str.islower()` and
`str.isupper()` test whole strings, so `"ab".islower()` is `True`. Without the length
check, `char_to_keys("ab")` returned `["ab"]`, a qcode that doesn't exist, and the error
surfaced later inside QEMU as something unrelated. Now it fails immediately, naming the
real problem.

## Chords

```python
parse_combo("ctrl-alt-f2")  → ["ctrl", "alt", "f2"]
parse_combo("ctrl--")       → ["ctrl", "minus"]
```

Chords are written with `-` between keys, which raises a question: how do you write
ctrl-plus-minus? qemu-mcp's answer is that `--` is a literal minus key. The tokenizer
walks the string one character at a time instead of calling `split("-")`, which
couldn't tell `ctrl-alt` from `ctrl` followed by a minus:

```
ctrl---alt  →  ctrl, -, alt
```

Named keys (`enter`, `esc`, `f1`…`f12`, arrows, `ctrl`/`alt`/`shift`) map through a
table. Single characters go back through `char_to_keys`, so `ctrl-C` presses
ctrl+shift+c, which is exactly what those characters mean.

## Sending

```python
qmp.command("send-key", keys=[{"type": "qcode", "data": k} for k in keys],
            **{"hold-time": hold_ms})
```

All keys in the list go down together, stay held for `hold-time` milliseconds, and come
up together. That's a chord. The demo sends `ctrl-c` this way, and the kernel, which
tracks ctrl state from make and break scancodes, prints `[ctrl-c]`: one keypress, not a
`c` with a stray ctrl.

## Validate Everything, Then Send

The first version of `type_text` translated and sent one character at a time:

```python
for ch in text:
    send(qmp, char_to_keys(ch))
```

Type `café` and it sends `c`, `a`, `f`, then raises on `é`. The guest now holds `caf`
in its line buffer. The agent got an error, and its next attempt at `café` types into a
line that already says `caf`. qemu-mcp's `qemu_type` had this hazard too: it translated
while it sent. Writing this module found it, and qemu-mcp now validates first (commit
`1d00b93`).

The fix is to translate the whole string first, so a bad character fails before
anything reaches the guest:

```python
presses = [char_to_keys(ch) for ch in text]
for keys in presses:
    send(qmp, keys)
```

The demo calls `type_text(None, "café")`. It passes `None` instead of a QMP connection,
so if any key were sent the call would crash on `None.command` rather than reach a
guest. It raises `ValueError` before the first send.

## Pacing

```python
time.sleep(delay)   # 20 ms between keys
```

The course kernel polls the PS/2 controller, and the 8042 holds one byte. QEMU queues
keys internally, but a polling guest that is busy elsewhere can still lose keystrokes if
they arrive too fast. Real interrupt-driven guests have larger buffers but the same
failure mode under load. A small fixed delay is the right trade: 30 characters take
about 0.6 s. qemu-mcp makes the delay a parameter.

## Checking What Was Typed

Typing is only half the job: you need to know it arrived. The course kernel echoes
completed lines to COM1 as `you typed: ...`, so each test can compare exact bytes:

```
kernel echoed: b'Hello, World! (x+y)*2 = "ok"'
```

That one line exercises uppercase, comma, `!`, both parentheses, `+`, `*`, `=` and
double quotes, which cover most of the shifted table. Backspace gets its own check:
`typo` plus backspace plus Enter must echo `typ`.

Also note the order in `main()`: `wait_for(log, b"ready")` comes first. Keys sent before
the kernel's keyboard loop starts are read by nobody (Module 01).

## Run It

```bash
python main.py --kernel ../kernel/kernel.elf
```

```
untypeable -> cannot type 'é': no key for it on a US keyboard
kernel echoed: b'Hello, World! (x+y)*2 = "ok"'
ctrl-c chord arrived as one keypress: [ctrl-c]
backspace edited the line: 'typo' -> 'typ'
OK
```
