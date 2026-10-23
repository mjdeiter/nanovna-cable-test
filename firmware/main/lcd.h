#pragma once

#include <stdbool.h>
#include <stdint.h>

#define LCD_WIDTH   320
#define LCD_HEIGHT  170

/* Brings up the panel. Returns false when no display is configured or init failed. */
bool lcd_init(void);

/* Pushes a full LCD_WIDTH x LCD_HEIGHT RGB565 frame and waits for the transfer to finish. */
void lcd_flush(const uint16_t *rgb565);
