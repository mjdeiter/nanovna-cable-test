#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

/* Starts the USB host stack and a task that keeps (re)connecting to the NanoVNA's CDC-ACM port. */
esp_err_t vna_usb_start(void);

bool vna_usb_connected(void);

/* Blocks until the NanoVNA is connected; returns whether it is. */
bool vna_usb_wait_connected(TickType_t ticks);

/*
 * Sends "<cmd>\r\n" and collects the reply into reply (NUL-terminated) until the shell prompt
 * "ch>" arrives. Returns ESP_ERR_TIMEOUT when the prompt does not arrive in time,
 * ESP_ERR_INVALID_STATE when the NanoVNA is not (or no longer) connected and ESP_ERR_NO_MEM when
 * the reply does not fit. reply_len receives the number of bytes collected either way.
 */
esp_err_t vna_usb_command(const char *cmd, char *reply, size_t reply_size, size_t *reply_len,
                          uint32_t timeout_ms);
