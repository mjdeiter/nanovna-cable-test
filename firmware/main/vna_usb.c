#include "vna_usb.h"

#include <stdio.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "usb/cdc_acm_host.h"
#include "usb/usb_host.h"

#include "vna_core.h"

static const char *TAG = "vna_usb";

#define EV_CONNECTED  (1u << 0)     /* device opened and ready */
#define EV_GONE       (1u << 1)     /* device unplugged (cleared on the next connect) */
#define EV_PROMPT     (1u << 2)     /* the current reply ended with the shell prompt */

#define DRAIN_MS      100           /* pause after a failed command so late bytes don't pollute the next reply */

static EventGroupHandle_t s_events;
static SemaphoreHandle_t  s_cmd_lock;   /* one command at a time; also guards s_dev */
static SemaphoreHandle_t  s_rx_lock;    /* guards the s_rx_* fields shared with the data callback */
static cdc_acm_dev_hdl_t  s_dev;
static char              *s_rx_dst;
static size_t             s_rx_cap;
static size_t             s_rx_len;
static bool               s_rx_overflow;
static bool               s_dirty;      /* the previous command ended abnormally */

/* Called by the CDC-ACM driver task for every chunk the NanoVNA sends. */
static bool handle_rx(const uint8_t *data, size_t len, void *arg)
{
    (void)arg;
    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    if (s_rx_dst) {
        if (len > s_rx_cap - s_rx_len) {
            s_rx_overflow = true;
            len = s_rx_cap - s_rx_len;
        }
        memcpy(s_rx_dst + s_rx_len, data, len);
        s_rx_len += len;
        s_rx_dst[s_rx_len] = '\0';
        if (vna_reply_complete(s_rx_dst, s_rx_len)) {
            xEventGroupSetBits(s_events, EV_PROMPT);
        }
    }
    xSemaphoreGive(s_rx_lock);
    return true;
}

static void handle_event(const cdc_acm_host_dev_event_data_t *event, void *user_ctx)
{
    (void)user_ctx;
    switch (event->type) {
    case CDC_ACM_HOST_ERROR:
        ESP_LOGE(TAG, "CDC-ACM error %d", event->data.error);
        break;
    case CDC_ACM_HOST_DEVICE_DISCONNECTED:
        ESP_LOGW(TAG, "NanoVNA disconnected");
        xEventGroupClearBits(s_events, EV_CONNECTED);
        xEventGroupSetBits(s_events, EV_GONE);
        break;
    default:
        break;
    }
}

/* Services the USB host library (device enumeration and so on). */
static void usb_lib_task(void *arg)
{
    (void)arg;
    while (true) {
        uint32_t flags = 0;

        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) {
            usb_host_device_free_all();
        }
    }
}

/* Opens the NanoVNA whenever it is plugged in and cleans up when it is unplugged. */
static void connect_task(void *arg)
{
    const cdc_acm_host_device_config_t dev_config = {
        .connection_timeout_ms = 1000,
        .out_buffer_size = 256,
        .in_buffer_size = 512,
        .event_cb = handle_event,
        .data_cb = handle_rx,
        .user_arg = NULL,
    };

    (void)arg;
    while (true) {
        cdc_acm_dev_hdl_t dev = NULL;
        esp_err_t err;

        xEventGroupClearBits(s_events, EV_GONE);      /* before opening, so an early unplug is not lost */
        err = cdc_acm_host_open(CONFIG_NANOVNA_USB_VID, CONFIG_NANOVNA_USB_PID, 0, &dev_config, &dev);

        if (err != ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(200));         /* not plugged in yet */
            continue;
        }
        ESP_LOGI(TAG, "NanoVNA connected (%04x:%04x)", CONFIG_NANOVNA_USB_VID, CONFIG_NANOVNA_USB_PID);

        /* Some CDC devices only talk once DTR is asserted; harmless for those that don't care */
        err = cdc_acm_host_set_control_line_state(dev, true, true);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "could not set DTR/RTS: %s", esp_err_to_name(err));
        }

        xSemaphoreTake(s_cmd_lock, portMAX_DELAY);
        s_dev = dev;
        xSemaphoreGive(s_cmd_lock);
        xEventGroupSetBits(s_events, EV_CONNECTED);

        xEventGroupWaitBits(s_events, EV_GONE, pdFALSE, pdFALSE, portMAX_DELAY);
        xEventGroupClearBits(s_events, EV_CONNECTED);   /* also covers an unplug that raced with the open */

        xSemaphoreTake(s_cmd_lock, portMAX_DELAY);  /* waits for a command in flight to give up */
        s_dev = NULL;
        cdc_acm_host_close(dev);
        xSemaphoreGive(s_cmd_lock);
    }
}

esp_err_t vna_usb_start(void)
{
    s_events = xEventGroupCreate();
    s_cmd_lock = xSemaphoreCreateMutex();
    s_rx_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_events && s_cmd_lock && s_rx_lock, ESP_ERR_NO_MEM, TAG, "sync objects");

    const usb_host_config_t host_config = {
        .skip_phy_setup = false,
        .intr_flags = ESP_INTR_FLAG_LEVEL1,
    };
    ESP_RETURN_ON_ERROR(usb_host_install(&host_config), TAG, "usb_host_install");
    ESP_RETURN_ON_FALSE(xTaskCreate(usb_lib_task, "usb_lib", 4096, NULL, 10, NULL) == pdPASS,
                        ESP_ERR_NO_MEM, TAG, "usb_lib task");
    ESP_RETURN_ON_ERROR(cdc_acm_host_install(NULL), TAG, "cdc_acm_host_install");
    ESP_RETURN_ON_FALSE(xTaskCreate(connect_task, "vna_conn", 4096, NULL, 5, NULL) == pdPASS,
                        ESP_ERR_NO_MEM, TAG, "connect task");
    return ESP_OK;
}

bool vna_usb_connected(void)
{
    return (xEventGroupGetBits(s_events) & EV_CONNECTED) != 0;
}

bool vna_usb_wait_connected(TickType_t ticks)
{
    return (xEventGroupWaitBits(s_events, EV_CONNECTED, pdFALSE, pdTRUE, ticks) & EV_CONNECTED) != 0;
}

esp_err_t vna_usb_command(const char *cmd, char *reply, size_t reply_size, size_t *reply_len,
                          uint32_t timeout_ms)
{
    char line[96];
    esp_err_t err;
    size_t len = 0;

    if (reply_size < 2 || snprintf(line, sizeof line, "%s\r\n", cmd) >= (int)sizeof line) {
        return ESP_ERR_INVALID_ARG;
    }
    if (xSemaphoreTake(s_cmd_lock, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    if (s_dev == NULL || !vna_usb_connected()) {
        err = ESP_ERR_INVALID_STATE;
    } else {
        if (s_dirty) {
            vTaskDelay(pdMS_TO_TICKS(DRAIN_MS));
        }

        xSemaphoreTake(s_rx_lock, portMAX_DELAY);       /* arm the receiver */
        s_rx_dst = reply;
        s_rx_cap = reply_size - 1;
        s_rx_len = 0;
        s_rx_overflow = false;
        reply[0] = '\0';
        xSemaphoreGive(s_rx_lock);
        xEventGroupClearBits(s_events, EV_PROMPT);

        err = cdc_acm_host_data_tx_blocking(s_dev, (const uint8_t *)line, strlen(line), 1000);
        if (err == ESP_OK) {
            EventBits_t bits = xEventGroupWaitBits(s_events, EV_PROMPT | EV_GONE, pdFALSE, pdFALSE,
                                                   pdMS_TO_TICKS(timeout_ms));
            if (bits & EV_PROMPT) {
                err = ESP_OK;
            } else if (bits & EV_GONE) {
                err = ESP_ERR_INVALID_STATE;
            } else {
                err = ESP_ERR_TIMEOUT;
            }
        }

        xSemaphoreTake(s_rx_lock, portMAX_DELAY);       /* disarm */
        s_rx_dst = NULL;
        len = s_rx_len;
        if (err == ESP_OK && s_rx_overflow) {
            err = ESP_ERR_NO_MEM;
        }
        xSemaphoreGive(s_rx_lock);
        s_dirty = (err != ESP_OK);
    }
    xSemaphoreGive(s_cmd_lock);

    if (reply_len) {
        *reply_len = len;
    }
    return err;
}
