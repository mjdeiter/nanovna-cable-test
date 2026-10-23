/* fbtext: tiny RGB565 framebuffer text/rect drawing (5x7 font) with no hardware dependencies. */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t *px;      /* width * height RGB565 pixels */
    int       width;
    int       height;
} fb_t;

#define FB_RGB(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | (((b) & 0xF8) >> 3)))

void fb_fill(fb_t *fb, uint16_t color);
void fb_rect(fb_t *fb, int x, int y, int w, int h, uint16_t color);

/*
 * Draws upper-case 5x7 text (lower case is folded, unknown characters draw as '?').
 * Each cell is 6*scale px wide (5 px glyph + 1 px gap) and 7*scale px high.
 * Returns the x position after the last cell.
 */
int fb_text(fb_t *fb, int x, int y, int scale, uint16_t color, const char *s);

/* Width in pixels of nchars characters, without the trailing gap */
int fb_text_width(int scale, size_t nchars);

#ifdef __cplusplus
}
#endif
