#include "vna_test.h"

#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "vna_usb.h"

#define REPLY_SIZE  6144        /* 101 lines of "re im" are about 2.5 KB */
#define MAX_POINTS  128

static char reply[REPLY_SIZE];
static vna_point_t s11[MAX_POINTS];
static vna_point_t s21[MAX_POINTS];

/* Runs one shell command into reply. On failure rep becomes an ERROR and false is returned. */
static bool command(const char *cmd, uint32_t timeout_ms, size_t *len, vna_report_t *rep)
{
    esp_err_t err = vna_usb_command(cmd, reply, sizeof reply, len, timeout_ms);

    if (err == ESP_OK) {
        return true;
    }
    rep->status = VNA_ERROR;
    switch (err) {
    case ESP_ERR_TIMEOUT:
        snprintf(rep->detail, sizeof rep->detail, "no 'ch>' prompt after '%s' (timeout or partial reply)", cmd);
        break;
    case ESP_ERR_INVALID_STATE:
        snprintf(rep->detail, sizeof rep->detail, "NanoVNA not connected");
        break;
    case ESP_ERR_NO_MEM:
        snprintf(rep->detail, sizeof rep->detail, "reply to '%s' too long", cmd);
        break;
    default:
        snprintf(rep->detail, sizeof rep->detail, "USB error on '%s': %s", cmd, esp_err_to_name(err));
        break;
    }
    return false;
}

static bool read_trace(int channel, vna_point_t *out, int *count, vna_report_t *rep)
{
    char cmd[16];
    char err[96];
    size_t len = 0;
    int n;

    snprintf(cmd, sizeof cmd, "data %d", channel);          /* data 0 = S11, data 1 = S21 */
    if (!command(cmd, 4000, &len, rep)) {
        return false;
    }
    n = vna_parse_trace(reply, len, cmd, out, MAX_POINTS, err, sizeof err);
    if (n < 0) {
        rep->status = VNA_ERROR;
        snprintf(rep->detail, sizeof rep->detail, "%s", err);
        return false;
    }
    *count = n;
    return true;
}

void vna_test_run(double max_vswr, double max_loss_db, bool check_cal, uint32_t settle_ms, vna_report_t *rep)
{
    char cmd[64];
    size_t len = 0;
    int n11 = 0;
    int n21 = 0;

    memset(rep, 0, sizeof *rep);

    /* Sweep range around 1 GHz (900 MHz to 1100 MHz, 101 points) */
    snprintf(cmd, sizeof cmd, "sweep %u %u %d", VNA_SWEEP_START_HZ, VNA_SWEEP_STOP_HZ, VNA_SWEEP_POINTS);
    if (!command(cmd, 3000, &len, rep)) {
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(settle_ms));       /* let at least one full sweep finish at the new range */

    /* Check calibration after the sweep command: changing the range can switch it off */
    if (check_cal) {
        char why[96];

        if (!command("cal", 3000, &len, rep)) {
            return;
        }
        if (!vna_cal_ok(reply, len, why, sizeof why)) {
            rep->status = VNA_INVALID;
            snprintf(rep->detail, sizeof rep->detail, "calibration not confirmed: %s", why);
            return;
        }
    }

    if (!read_trace(0, s11, &n11, rep) || !read_trace(1, s21, &n21, rep)) {
        return;
    }
    vna_evaluate(s11, (size_t)n11, s21, (size_t)n21, max_vswr, max_loss_db, rep);
    if (!check_cal && (rep->status == VNA_PASS || rep->status == VNA_FAIL)) {
        snprintf(rep->detail, sizeof rep->detail, "cal check skipped");
    }
}
