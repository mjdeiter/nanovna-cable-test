/*
 * NanoVNA cable tester for ESP32-S3: the S3 is the USB host, a NanoVNA-H/H4 is the device.
 * Press the button to sweep 900-1100 MHz and get PASS/FAIL for VSWR and insertion loss at 1.0 GHz.
 */
#include <stdbool.h>
#include <stdio.h>

#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "display.h"
#include "vna_core.h"
#include "vna_test.h"
#include "vna_usb.h"

static const char *TAG = "nanovna";

#define MAX_VSWR     (CONFIG_NANOVNA_MAX_VSWR_X100 / 100.0)
#define MAX_LOSS_DB  (CONFIG_NANOVNA_MAX_LOSS_X100 / 100.0)
#define BUTTON_GPIO  CONFIG_NANOVNA_BUTTON_GPIO

#ifdef CONFIG_NANOVNA_SKIP_CAL_CHECK
#define CHECK_CAL false
#else
#define CHECK_CAL true
#endif

static void button_init(void)
{
    const gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };

    ESP_ERROR_CHECK(gpio_config(&cfg));
}

/* One press = one test: debounced, and waits for the button to be released. */
static bool button_pressed(void)
{
    if (gpio_get_level(BUTTON_GPIO) != 0) {
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(30));
    if (gpio_get_level(BUTTON_GPIO) != 0) {
        return false;
    }
    for (int i = 0; i < 100 && gpio_get_level(BUTTON_GPIO) == 0; i++) {
        vTaskDelay(pdMS_TO_TICKS(30));
    }
    return true;
}

static bool sync_vna(void)
{
    static char reply[64];
    size_t len = 0;

    /* A bare CR/LF makes the NanoVNA shell answer with its prompt */
    return vna_usb_command("", reply, sizeof reply, &len, 2000) == ESP_OK;
}

static void log_report(const vna_report_t *r)
{
    if (r->have_values) {
        ESP_LOGI(TAG, "RESULT %s vswr=%.2f loss_db=%.2f (limits %.2f / %.2f dB) %s",
                 vna_status_name(r->status), r->vswr, r->loss_db, MAX_VSWR, MAX_LOSS_DB, r->detail);
    } else {
        ESP_LOGI(TAG, "RESULT %s %s", vna_status_name(r->status), r->detail);
    }
}

void app_main(void)
{
    static vna_report_t report;
    bool ready = false;

    display_init();
    display_banner("NANOVNA TESTER", "STARTING", "");
    button_init();
    ESP_ERROR_CHECK(vna_usb_start());

    while (true) {
        if (!vna_usb_connected()) {
            ready = false;
            display_banner("PLUG IN THE NANOVNA", "WAITING FOR USB", "");
            ESP_LOGI(TAG, "waiting for the NanoVNA on USB");
            vna_usb_wait_connected(portMAX_DELAY);
        }
        if (!ready) {
            if (!sync_vna()) {
                display_banner("NANOVNA DOES NOT", "ANSWER, RETRYING", "");
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
            ready = true;
            display_ready(MAX_VSWR, MAX_LOSS_DB);
            ESP_LOGI(TAG, "NanoVNA ready: press the button (GPIO%d) to test", BUTTON_GPIO);
        }
        if (button_pressed()) {
            display_banner("TESTING", "", "");
            vna_test_run(MAX_VSWR, MAX_LOSS_DB, CHECK_CAL, CONFIG_NANOVNA_SETTLE_MS, &report);
            log_report(&report);
            display_report(&report, MAX_VSWR, MAX_LOSS_DB);
        }
        vTaskDelay(pdMS_TO_TICKS(30));
    }
}
