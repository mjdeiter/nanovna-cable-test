/* Host-side tests for fbtext. Run with "--art" to print the glyphs as ASCII art. */
#include <stdio.h>
#include <string.h>

#include "fbtext.h"

static int checks;
static int fails;

#define CHECK(cond) do { checks++; if (!(cond)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

#define MAXW 400

static void render(uint16_t *px, int w, int h, int scale, const char *s)
{
    fb_t fb = {px, w, h};

    fb_fill(&fb, 0);
    fb_text(&fb, 0, 0, scale, 0xFFFF, s);
}

static void art(const char *s)
{
    static uint16_t px[MAXW * 7];
    int w = fb_text_width(1, strlen(s));

    render(px, w, 7, 1, s);
    for (int y = 0; y < 7; y++) {
        for (int x = 0; x < w; x++) {
            putchar(px[y * w + x] ? '#' : '.');
        }
        putchar('\n');
    }
    putchar('\n');
}

static void test_basics(void)
{
    static uint16_t a[60 * 7];
    static uint16_t b[60 * 7];

    CHECK(FB_RGB(255, 0, 0) == 0xF800);
    CHECK(FB_RGB(0, 255, 0) == 0x07E0);
    CHECK(FB_RGB(0, 0, 255) == 0x001F);
    CHECK(FB_RGB(255, 255, 255) == 0xFFFF);

    CHECK(fb_text_width(2, 4) == 4 * 12 - 2);
    CHECK(fb_text_width(8, 0) == 0);

    /* 'I' = columns 00 41 7F 41 00 */
    render(a, 5, 7, 1, "I");
    CHECK(a[3 * 5 + 2] != 0);       /* centre of the stem */
    CHECK(a[0 * 5 + 1] != 0);       /* top serif */
    CHECK(a[6 * 5 + 3] != 0);       /* bottom serif */
    CHECK(a[3 * 5 + 0] == 0);       /* empty left column */

    /* scaling: a pixel becomes a scale x scale block */
    {
        static uint16_t s3[5 * 3 * 7 * 3];

        render(s3, 15, 21, 3, "I");
        CHECK(s3[(3 * 3 + 1) * 15 + (2 * 3 + 1)] != 0);
        CHECK(s3[(3 * 3 + 1) * 15 + (0 * 3 + 1)] == 0);
    }

    /* lower case folds, unknown characters become '?' */
    render(a, 6, 7, 1, "a");
    render(b, 6, 7, 1, "A");
    CHECK(memcmp(a, b, sizeof a[0] * 6 * 7) == 0);
    render(a, 6, 7, 1, "@");
    render(b, 6, 7, 1, "?");
    CHECK(memcmp(a, b, sizeof a[0] * 6 * 7) == 0);
}

static void test_clipping(void)
{
    uint16_t buf[3 + 10 * 10 + 3];
    fb_t fb = {buf + 3, 10, 10};
    int lit = 0;

    memset(buf, 0xAA, sizeof buf);
    fb_fill(&fb, 0);
    fb_rect(&fb, -2, -2, 5, 5, 1);                      /* only the 3x3 corner is inside */
    for (int i = 0; i < 100; i++) {
        lit += fb.px[i] == 1;
    }
    CHECK(lit == 9);

    fb_text(&fb, -20, 2, 3, 1, "CLIP");                 /* partly off the left edge */
    fb_text(&fb, 5, 5, 4, 1, "OFF");                    /* runs off the right and bottom edges */
    fb_text(&fb, 100, 100, 2, 1, "GONE");               /* fully outside */
    CHECK(buf[0] == 0xAAAA && buf[1] == 0xAAAA && buf[2] == 0xAAAA);                  /* guard words intact */
    CHECK(buf[103] == 0xAAAA && buf[104] == 0xAAAA && buf[105] == 0xAAAA);
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--art") == 0) {
        art("ABCDEFGHIJKLM");
        art("NOPQRSTUVWXYZ");
        art("0123456789.:-");
        art("!'(),/<=>?%+");
        art("PASS FAIL INVALID");
        art("[];_ NOT: (CAL REPLY; 1,2)");
        return 0;
    }
    test_basics();
    test_clipping();
    printf("fbtext: %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
