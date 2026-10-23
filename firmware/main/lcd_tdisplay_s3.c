#include "lcd.h"

#include "sdkconfig.h"

#ifdef CONFIG_NANOVNA_DISPLAY_TDISPLAY_S3

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_lcd_io_i80.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "lcd";

/* LilyGO T-Display-S3: ST7789 (170x320) on an 8-bit parallel (i80) bus */
#define PIN_POWER_ON   15      /* must be high to power the display */
#define PIN_BACKLIGHT  38
#define PIN_RST        5
#define PIN_CS         6
#define PIN_DC         7
#define PIN_WR         8
#define PIN_RD         9       /* unused for writes: held high */

#ifdef CONFIG_NANOVNA_LCD_INVERT_COLORS
#define LCD_INVERT true
#else
#define LCD_INVERT false
#endif
#ifdef CONFIG_NANOVNA_LCD_SWAP_XY
#define LCD_SWAP_XY true
#else
#define LCD_SWAP_XY false
#endif
#ifdef CONFIG_NANOVNA_LCD_MIRROR_X
#define LCD_MIRROR_X true
#else
#define LCD_MIRROR_X false
#endif
#ifdef CONFIG_NANOVNA_LCD_MIRROR_Y
#define LCD_MIRROR_Y true
#else
#define LCD_MIRROR_Y false
#endif

static esp_lcd_panel_handle_t s_panel;
static SemaphoreHandle_t s_done;

static bool on_color_done(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *event, void *ctx)
{
    BaseType_t woken = pdFALSE;

    (void)io;
    (void)event;
    xSemaphoreGiveFromISR((SemaphoreHandle_t)ctx, &woken);
    return woken == pdTRUE;
}

static esp_err_t panel_setup(void)
{
    s_done = xSemaphoreCreateBinary();
    ESP_RETURN_ON_FALSE(s_done, ESP_ERR_NO_MEM, TAG, "semaphore");

    const gpio_config_t out = {
        .pin_bit_mask = (1ULL << PIN_POWER_ON) | (1ULL << PIN_BACKLIGHT) | (1ULL << PIN_RD),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&out), TAG, "gpio config");
    gpio_set_level(PIN_POWER_ON, 1);
    gpio_set_level(PIN_RD, 1);
    gpio_set_level(PIN_BACKLIGHT, 0);

    esp_lcd_i80_bus_handle_t bus = NULL;
    const esp_lcd_i80_bus_config_t bus_cfg = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .dc_gpio_num = PIN_DC,
        .wr_gpio_num = PIN_WR,
        .data_gpio_nums = {39, 40, 41, 42, 45, 46, 47, 48},
        .bus_width = 8,
        .max_transfer_bytes = LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t),
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_i80_bus(&bus_cfg, &bus), TAG, "i80 bus");

    esp_lcd_panel_io_handle_t io = NULL;
    const esp_lcd_panel_io_i80_config_t io_cfg = {
        .cs_gpio_num = PIN_CS,
        .pclk_hz = 20 * 1000 * 1000,
        .trans_queue_depth = 4,
        .on_color_trans_done = on_color_done,
        .user_ctx = s_done,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .dc_levels = {.dc_idle_level = 0, .dc_cmd_level = 0, .dc_dummy_level = 0, .dc_data_level = 1},
        .flags = {.swap_color_bytes = 1},
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i80(bus, &io_cfg, &io), TAG, "panel io");

    const esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = PIN_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io, &panel_cfg, &s_panel), TAG, "st7789");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG, "reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG, "init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_panel, LCD_INVERT), TAG, "invert");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_swap_xy(s_panel, LCD_SWAP_XY), TAG, "swap xy");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(s_panel, LCD_MIRROR_X, LCD_MIRROR_Y), TAG, "mirror");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(s_panel, CONFIG_NANOVNA_LCD_GAP_X, CONFIG_NANOVNA_LCD_GAP_Y), TAG, "gap");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_panel, true), TAG, "display on");
    gpio_set_level(PIN_BACKLIGHT, 1);
    return ESP_OK;
}

bool lcd_init(void)
{
    esp_err_t err = panel_setup();

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "display init failed: %s", esp_err_to_name(err));
        s_panel = NULL;
        return false;
    }
    return true;
}

void lcd_flush(const uint16_t *rgb565)
{
    if (!s_panel) {
        return;
    }
    xSemaphoreTake(s_done, 0);                      /* drop a stale completion */
    if (esp_lcd_panel_draw_bitmap(s_panel, 0, 0, LCD_WIDTH, LCD_HEIGHT, rgb565) == ESP_OK) {
        xSemaphoreTake(s_done, pdMS_TO_TICKS(500));
    }
}

#else   /* no display configured: results go to the console only */

bool lcd_init(void)
{
    return false;
}

void lcd_flush(const uint16_t *rgb565)
{
    (void)rgb565;
}

#endif
