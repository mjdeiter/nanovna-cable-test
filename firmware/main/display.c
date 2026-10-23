#include "display.h"

#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"

#include "fbtext.h"
#include "lcd.h"

#define COL_BLACK  FB_RGB(0, 0, 0)
#define COL_WHITE  FB_RGB(255, 255, 255)
#define COL_LIGHT  FB_RGB(215, 215, 215)
#define COL_DIM    FB_RGB(140, 140, 140)
#define COL_PASS   FB_RGB(0, 110, 0)
#define COL_FAIL   FB_RGB(170, 0, 0)
#define COL_INVAL  FB_RGB(170, 105, 0)
#define COL_ERROR  FB_RGB(110, 0, 140)

#define WRAP_CHARS  26      /* characters per line at scale 2 on a 320 px wide screen */
#define WRAP_LINES  5

static fb_t s_fb;
static bool s_ready;

void display_init(void)
{
    if (!lcd_init()) {
        return;
    }
    s_fb.width = LCD_WIDTH;
    s_fb.height = LCD_HEIGHT;
    s_fb.px = heap_caps_malloc((size_t)LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    s_ready = s_fb.px != NULL;
}

static void centered(int y, int scale, uint16_t color, const char *s)
{
    int w = fb_text_width(scale, strlen(s));

    fb_text(&s_fb, (s_fb.width - w) / 2, y, scale, color, s);
}

/* Word-wraps text into lines of at most max_chars (break at a space when there is one). */
static int wrap(const char *text, int max_chars, char lines[][32], int max_lines)
{
    int n = 0;
    const char *p = text;

    while (*p && n < max_lines) {
        while (*p == ' ') {
            p++;
        }
        size_t remaining = strlen(p);
        if (remaining == 0) {
            break;
        }
        size_t take = remaining < (size_t)max_chars ? remaining : (size_t)max_chars;
        if (take < remaining) {
            size_t cut = take;
            while (cut > 0 && p[cut] != ' ') {
                cut--;
            }
            if (cut > 0) {
                take = cut;
            }
        }
        memcpy(lines[n], p, take);
        lines[n][take] = '\0';
        n++;
        p += take;
    }
    return n;
}

void display_banner(const char *line1, const char *line2, const char *line3)
{
    if (!s_ready) {
        return;
    }
    fb_fill(&s_fb, COL_BLACK);
    centered(22, 2, COL_WHITE, line1);
    centered(70, 2, COL_WHITE, line2);
    centered(118, 2, COL_DIM, line3);
    lcd_flush(s_fb.px);
}

void display_ready(double max_vswr, double max_loss_db)
{
    char limits[40];

    snprintf(limits, sizeof limits, "VSWR<=%.2f LOSS<=%.2fDB", max_vswr, max_loss_db);
    display_banner("READY", "PRESS BUTTON TO TEST", limits);
}

void display_report(const vna_report_t *r, double max_vswr, double max_loss_db)
{
    char buf[48];
    bool measured = r->status == VNA_PASS || r->status == VNA_FAIL;

    if (!s_ready) {
        return;
    }
    switch (r->status) {
    case VNA_PASS:    fb_fill(&s_fb, COL_PASS);  break;
    case VNA_FAIL:    fb_fill(&s_fb, COL_FAIL);  break;
    case VNA_INVALID: fb_fill(&s_fb, COL_INVAL); break;
    default:          fb_fill(&s_fb, COL_ERROR); break;
    }

    if (measured) {
        centered(8, 8, COL_WHITE, vna_status_name(r->status));
        snprintf(buf, sizeof buf, "VSWR %.2f", r->vswr);
        centered(76, 3, COL_WHITE, buf);
        snprintf(buf, sizeof buf, "LOSS %.2f DB", r->loss_db);
        centered(102, 3, COL_WHITE, buf);
        snprintf(buf, sizeof buf, "MAX %.2f / %.2f DB", max_vswr, max_loss_db);
        centered(132, 2, COL_LIGHT, buf);
        if (r->detail[0]) {
            centered(152, 2, COL_WHITE, r->detail);
        }
    } else {
        char lines[WRAP_LINES][32];
        int n;

        centered(6, 6, COL_WHITE, vna_status_name(r->status));
        n = wrap(r->detail, WRAP_CHARS, lines, WRAP_LINES);
        for (int i = 0; i < n; i++) {
            centered(58 + i * 18, 2, COL_WHITE, lines[i]);
        }
        if (r->have_values) {
            snprintf(buf, sizeof buf, "VSWR %.2f LOSS %.2f DB", r->vswr, r->loss_db);
            centered(150, 2, COL_LIGHT, buf);
        }
    }
    lcd_flush(s_fb.px);
}
