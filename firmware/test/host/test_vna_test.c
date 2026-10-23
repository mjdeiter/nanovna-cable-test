/* Host test of the measurement sequence in main/vna_test.c, using a scripted fake NanoVNA. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "freertos/task.h"
#include "vna_test.h"
#include "vna_usb.h"

static int checks;
static int fails;

#define CHECK(cond) do { checks++; if (!(cond)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #cond); } } while (0)
#define CHECK_HAS(hay, needle) do { checks++; if (!strstr((hay), (needle))) { fails++; \
    printf("FAIL line %d: \"%s\" lacks \"%s\"\n", __LINE__, (hay), (needle)); } } while (0)

/* ---- scripted fake transport ---- */
static struct {
    const char *cal_reply;      /* body of the reply to "cal" (NULL = fully calibrated) */
    double      s11_re;
    double      s21_re;
    int         points;         /* points per data reply */
    const char *fail_cmd;       /* commands starting with this fail with fail_err ... */
    esp_err_t   fail_err;
    const char *garbage_cmd;    /* ... or get a malformed line appended */
    char        cmds[8][64];
    int         ncmds;
    unsigned    delays[4];
    int         ndelays;
} fake;

static void fake_reset(void)
{
    memset(&fake, 0, sizeof fake);
    fake.points = 101;
    fake.s11_re = 0.10;             /* VSWR 1.22 */
    fake.s21_re = 0.95;             /* 0.45 dB */
}

void vTaskDelay(TickType_t ticks)
{
    if (fake.ndelays < 4) {
        fake.delays[fake.ndelays++] = ticks;
    }
}

esp_err_t vna_usb_command(const char *cmd, char *reply, size_t reply_size, size_t *reply_len, uint32_t timeout_ms)
{
    size_t len = 0;

    (void)timeout_ms;
    if (fake.ncmds < 8) {
        snprintf(fake.cmds[fake.ncmds++], sizeof fake.cmds[0], "%s", cmd);
    }
    if (fake.fail_cmd && strncmp(cmd, fake.fail_cmd, strlen(fake.fail_cmd)) == 0) {
        reply[0] = '\0';
        *reply_len = 0;
        return fake.fail_err;
    }

    len += (size_t)snprintf(reply + len, reply_size - len, "%s\r\n", cmd);          /* echo */
    if (strcmp(cmd, "cal") == 0) {
        len += (size_t)snprintf(reply + len, reply_size - len, "%s\r\n",
                                fake.cal_reply ? fake.cal_reply : "load open short thru isoln Es Er Et cal'ed");
    } else if (strncmp(cmd, "data ", 5) == 0) {
        double re = strcmp(cmd, "data 0") == 0 ? fake.s11_re : fake.s21_re;

        for (int i = 0; i < fake.points; i++) {
            len += (size_t)snprintf(reply + len, reply_size - len, "%.6f %.6f\r\n", re, 0.0);
        }
        if (fake.garbage_cmd && strcmp(cmd, fake.garbage_cmd) == 0) {
            len += (size_t)snprintf(reply + len, reply_size - len, "oops\r\n");
        }
    }
    len += (size_t)snprintf(reply + len, reply_size - len, "ch> ");
    *reply_len = len;
    return ESP_OK;
}

/* ---- tests ---- */
static vna_report_t run(bool check_cal)
{
    vna_report_t rep;

    vna_test_run(1.60, 0.58, check_cal, 600, &rep);
    return rep;
}

static void test_normal(void)
{
    vna_report_t r;

    fake_reset();
    r = run(true);
    CHECK(r.status == VNA_PASS && r.have_values);
    CHECK(fabs(r.vswr - 1.2222) < 1e-3 && fabs(r.loss_db - 0.4455) < 1e-3);
    CHECK(r.detail[0] == '\0');
    CHECK(fake.ncmds == 4);
    CHECK(strcmp(fake.cmds[0], "sweep 900000000 1100000000 101") == 0);
    CHECK(strcmp(fake.cmds[1], "cal") == 0);
    CHECK(strcmp(fake.cmds[2], "data 0") == 0);
    CHECK(strcmp(fake.cmds[3], "data 1") == 0);
    CHECK(fake.ndelays == 1 && fake.delays[0] == 600);              /* settle time after the sweep command */

    fake_reset();
    fake.s11_re = 0.30;
    CHECK(run(true).status == VNA_FAIL);                           /* VSWR 1.86 */
    fake_reset();
    fake.s21_re = 0.90;
    CHECK(run(true).status == VNA_FAIL);                           /* 0.92 dB */
}

static void test_errors(void)
{
    static const struct { const char *cmd; esp_err_t err; const char *needle; } cases[] = {
        {"sweep",  ESP_ERR_TIMEOUT,       "no 'ch>' prompt after 'sweep"},
        {"cal",    ESP_ERR_TIMEOUT,       "after 'cal'"},
        {"data 0", ESP_ERR_TIMEOUT,       "after 'data 0'"},
        {"data 1", ESP_ERR_TIMEOUT,       "after 'data 1'"},
        {"sweep",  ESP_ERR_INVALID_STATE, "not connected"},
        {"data 0", ESP_ERR_NO_MEM,        "too long"},
        {"data 1", ESP_FAIL,              "USB error"},
    };
    vna_report_t r;

    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        fake_reset();
        fake.fail_cmd = cases[i].cmd;
        fake.fail_err = cases[i].err;
        r = run(true);
        CHECK(r.status == VNA_ERROR && !r.have_values);
        CHECK_HAS(r.detail, cases[i].needle);
    }

    fake_reset();
    fake.fail_cmd = "sweep";
    fake.fail_err = ESP_ERR_TIMEOUT;
    run(true);
    CHECK(fake.ncmds == 1);                                         /* gives up at the first failure */

    fake_reset();
    fake.garbage_cmd = "data 1";
    r = run(true);
    CHECK(r.status == VNA_ERROR);
    CHECK_HAS(r.detail, "malformed");

    fake_reset();
    fake.points = 60;
    r = run(true);
    CHECK(r.status == VNA_ERROR);
    CHECK_HAS(r.detail, "expected 101");
}

static void test_calibration_gate(void)
{
    vna_report_t r;

    fake_reset();
    fake.cal_reply = "load open short thru isoln Es Er Et";        /* not applied */
    r = run(true);
    CHECK(r.status == VNA_INVALID);
    CHECK_HAS(r.detail, "calibration not confirmed");
    CHECK_HAS(r.detail, "not applied");
    CHECK(fake.ncmds == 2);                                         /* sweep, cal: no data is read */

    fake_reset();
    fake.cal_reply = "";
    r = run(true);
    CHECK(r.status == VNA_INVALID);
    CHECK_HAS(r.detail, "no calibration stored");

    fake_reset();                                                   /* check disabled: no "cal" command, result is marked */
    fake.cal_reply = "";
    r = run(false);
    CHECK(r.status == VNA_PASS);
    CHECK(strcmp(r.detail, "cal check skipped") == 0);
    CHECK(fake.ncmds == 3 && strcmp(fake.cmds[1], "data 0") == 0);

    fake_reset();
    fake.s21_re = 1.2;                                              /* S21 gain */
    r = run(true);
    CHECK(r.status == VNA_INVALID && r.have_values);
    CHECK_HAS(r.detail, "gain");
}

int main(void)
{
    test_normal();
    test_errors();
    test_calibration_gate();
    printf("vna_test: %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
