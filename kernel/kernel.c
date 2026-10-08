/* The course's test kernel: a tiny console you can drive from qemu-mcp.
 *
 * - Boots with a progress bar on the VGA text screen, then settles
 *   (Module 03 waits for the screen to stop changing).
 * - Echoes PS/2 keyboard input to the screen and to COM1 (Module 04).
 * - Echoes COM1 input back as "serial got: <line>" (Module 05).
 * - Commands typed on either console:
 *     poweroff   ACPI shutdown on QEMU's pc machine (Module 08)
 *     count N    prints count=N on serial, so snapshots can be told apart
 * Everything is polled: no interrupts, no IDT.
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

static inline void outb(u16 port, u8 v) { __asm__ volatile("outb %0, %1" : : "a"(v), "Nd"(port)); }
static inline void outw(u16 port, u16 v) { __asm__ volatile("outw %0, %1" : : "a"(v), "Nd"(port)); }
static inline u8 inb(u16 port) { u8 v; __asm__ volatile("inb %1, %0" : "=a"(v) : "Nd"(port)); return v; }

/* ---- COM1 ------------------------------------------------------------- */
#define COM1 0x3F8

static void serial_init(void) {
    outb(COM1 + 1, 0x00);   /* no interrupts */
    outb(COM1 + 3, 0x80);   /* DLAB on */
    outb(COM1 + 0, 0x01);   /* 115200 baud */
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03);   /* 8N1, DLAB off */
    outb(COM1 + 4, 0x03);   /* DTR + RTS */
}

static void serial_putc(char c) {
    while (!(inb(COM1 + 5) & 0x20)) { }
    outb(COM1, (u8)c);
}

static void serial_puts(const char *s) {
    while (*s) {
        if (*s == '\n') serial_putc('\r');
        serial_putc(*s++);
    }
}

static int serial_ready(void) { return inb(COM1 + 5) & 0x01; }

/* ---- VGA text mode ---------------------------------------------------- */
static volatile u16 *const vga = (volatile u16 *)0xB8000;
static int row = 0, col = 0;
#define ATTR 0x0F00

static void vga_clear(void) {
    for (int i = 0; i < 80 * 25; i++) vga[i] = ATTR | ' ';
    row = col = 0;
}

static void vga_putc(char c) {
    if (c == '\n') { col = 0; row++; }
    else if (c == '\b') { if (col > 2) { col--; vga[row * 80 + col] = ATTR | ' '; } }
    else { vga[row * 80 + col] = (u16)(ATTR | (u8)c); col++; }
    if (col >= 80) { col = 0; row++; }
    if (row >= 25) {
        for (int i = 0; i < 80 * 24; i++) vga[i] = vga[i + 80];
        for (int i = 80 * 24; i < 80 * 25; i++) vga[i] = ATTR | ' ';
        row = 24;
    }
}

static void vga_puts(const char *s) { while (*s) vga_putc(*s++); }

static void both_puts(const char *s) { vga_puts(s); serial_puts(s); }

/* ---- PS/2 keyboard (scancode set 1, US layout) ----------------------- */
static const char plain[128] = {
    0, 27, '1','2','3','4','5','6','7','8','9','0','-','=','\b','\t',
    'q','w','e','r','t','y','u','i','o','p','[',']','\n', 0,
    'a','s','d','f','g','h','j','k','l',';','\'','`', 0,'\\',
    'z','x','c','v','b','n','m',',','.','/', 0,'*', 0,' ',
};
static const char shifted[128] = {
    0, 27, '!','@','#','$','%','^','&','*','(',')','_','+','\b','\t',
    'Q','W','E','R','T','Y','U','I','O','P','{','}','\n', 0,
    'A','S','D','F','G','H','J','K','L',':','"','~', 0,'|',
    'Z','X','C','V','B','N','M','<','>','?', 0,'*', 0,' ',
};
static int shift = 0, ctrl = 0;

/* Returns a character, or 0 if no complete keypress is waiting. */
static char kbd_poll(void) {
    if (!(inb(0x64) & 0x01)) return 0;
    u8 sc = inb(0x60);
    if (sc == 0x2A || sc == 0x36) { shift = 1; return 0; }
    if (sc == 0xAA || sc == 0xB6) { shift = 0; return 0; }
    if (sc == 0x1D) { ctrl = 1; return 0; }
    if (sc == 0x9D) { ctrl = 0; return 0; }
    if (sc & 0x80) return 0;                       /* other key releases */
    char c = shift ? shifted[sc] : plain[sc];
    if (ctrl && c >= 'a' && c <= 'z') {            /* ctrl-letter chords */
        both_puts("\n[ctrl-");
        char s[2] = {c, 0};
        both_puts(s);
        both_puts("]\n> ");
        return 0;
    }
    return c;
}

/* ---- tiny command handling -------------------------------------------- */
static int streq(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static int starts(const char *s, const char *p) {
    while (*p) if (*s++ != *p++) return 0;
    return 1;
}

static void run_command(const char *line, const char *source) {
    if (streq(line, "poweroff")) {
        both_puts("powering off\n");
        outw(0x604, 0x2000);       /* QEMU pc: PM1a_CNT, SLP_TYPa=0 | SLP_EN */
        for (;;) __asm__ volatile("hlt");
    }
    if (starts(line, "count ")) {
        serial_puts("count=");
        serial_puts(line + 6);
        serial_puts("\n");
        return;
    }
    serial_puts(source);
    serial_puts(line);
    serial_puts("\n");
}

static void progress_bar(void) {
    /* Animate for a while so the screen is visibly "busy" during boot. */
    vga_puts("booting [");
    for (int i = 0; i < 40; i++) {
        vga_putc('#');
        for (volatile u32 d = 0; d < 15000000; d++) { }
    }
    vga_puts("]\n");
}

void kmain(void) {
    serial_init();
    vga_clear();
    serial_puts("kernel: hello from COM1\n");
    progress_bar();
    both_puts("ready\n> ");

    char kline[80], sline[80];
    int klen = 0, slen = 0;
    for (;;) {
        char c = kbd_poll();
        if (c) {
            if (c == '\n') {
                kline[klen] = 0;
                vga_putc('\n');
                serial_puts("\n");
                run_command(kline, "you typed: ");
                klen = 0;
                vga_puts("> ");
            } else if (c == '\b') {
                if (klen) { klen--; vga_putc('\b'); }
            } else if (klen < 79) {
                kline[klen++] = c;
                vga_putc(c);
                char s[2] = {c, 0};
                serial_puts(s);
            }
        }
        if (serial_ready()) {
            char s = (char)inb(COM1);
            if (s == '\r' || s == '\n') {
                if (slen) {
                    sline[slen] = 0;
                    run_command(sline, "serial got: ");
                    slen = 0;
                }
            } else if (slen < 79) {
                sline[slen++] = s;
            }
        }
    }
}
