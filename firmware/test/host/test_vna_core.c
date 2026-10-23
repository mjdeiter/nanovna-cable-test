/* Host-side unit tests for vna_core (no hardware, no ESP-IDF). Run with: make -C firmware/test/host test */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "vna_core.h"

static int checks;
static int fails;

#define CHECK(cond) do { checks++; if (!(cond)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #cond); } } while (0)
#define CHECK_NEAR(a, b, tol) do { checks++; double a_ = (a), b_ = (b); \
    if (!(fabs(a_ - b_) <= (tol))) { fails++; printf("FAIL line %d: %s = %g, want %g\n", __LINE__, #a, a_, b_); } } while (0)
#define CHECK_HAS(hay, needle) do { checks++; if (!strstr((hay), (needle))) { fails++; \
    printf("FAIL line %d: \"%s\" lacks \"%s\"\n", __LINE__, (hay), (needle)); } } while (0)

/* What a NanoVNA sends back for a data command */
static size_t make_reply(char *buf, size_t size, const char *cmd, bool echo,
                         const vna_point_t *pts, int n, bool prompt)
{
    size_t len = 0;

    if (echo) {
        len += (size_t)snprintf(buf + len, size - len, "%s\r\n", cmd);
    }
    for (int i = 0; i < n; i++) {
        len += (size_t)snprintf(buf + len, size - len, "%.6f %.6f\r\n", pts[i].re, pts[i].im);
    }
    if (prompt) {
        len += (size_t)snprintf(buf + len, size - len, "ch> ");
    }
    return len;
}

static void fill(vna_point_t *p, int n, double re, double im)
{
    for (int i = 0; i < n; i++) {
        p[i].re = re;
        p[i].im = im;
    }
}

static void test_prompt(void)
{
    CHECK(vna_reply_complete("ch> ", 4));
    CHECK(vna_reply_complete("sweep 1 2 3\r\nch>", 16));
    CHECK(!vna_reply_complete("ch", 2));
    CHECK(!vna_reply_complete("", 0));
    CHECK(!vna_reply_complete("data 0\r\n0.1 0.2\r\n", 17));
}

static void test_parse(void)
{
    static char buf[8192];
    static vna_point_t pts[128];
    static vna_point_t idx[101];
    char err[96] = "";
    size_t len;

    for (int i = 0; i < 101; i++) {
        idx[i].re = i / 1000.0;
        idx[i].im = -i / 1000.0;
    }

    len = make_reply(buf, sizeof buf, "data 0", true, idx, 101, true);
    CHECK(vna_reply_complete(buf, len));
    CHECK(vna_parse_trace(buf, len, "data 0", pts, 128, err, sizeof err) == 101);
    CHECK_NEAR(pts[50].re, 0.050, 1e-9);
    CHECK_NEAR(pts[50].im, -0.050, 1e-9);
    CHECK_NEAR(pts[100].re, 0.100, 1e-9);

    len = make_reply(buf, sizeof buf, "data 0", false, idx, 101, true);              /* no echo */
    CHECK(vna_parse_trace(buf, len, "data 0", pts, 128, err, sizeof err) == 101);

    /* stale prompt and blank lines before the echo */
    {
        static char buf2[8192];
        size_t l2 = (size_t)snprintf(buf2, sizeof buf2, "\r\nch> \r\n");
        l2 += make_reply(buf2 + l2, sizeof buf2 - l2, "data 1", true, idx, 101, true);
        CHECK(vna_parse_trace(buf2, l2, "data 1", pts, 128, err, sizeof err) == 101);
    }

    /* a reply cut off mid-stream has no prompt, so the caller can tell */
    len = make_reply(buf, sizeof buf, "data 0", true, idx, 101, true);
    CHECK(!vna_reply_complete(buf, 300));

    /* a short but prompt-terminated reply parses to fewer points (vna_evaluate rejects it) */
    len = make_reply(buf, sizeof buf, "data 0", true, idx, 60, true);
    CHECK(vna_parse_trace(buf, len, "data 0", pts, 128, err, sizeof err) == 60);

    /* malformed lines */
    CHECK(vna_parse_trace("data 0\r\n0.1 abc\r\nch> ", 22, "data 0", pts, 128, err, sizeof err) == -1);
    CHECK_HAS(err, "malformed");
    CHECK(vna_parse_trace("data 0\r\n0.1\r\nch> ", 18, "data 0", pts, 128, err, sizeof err) == -1);
    CHECK(vna_parse_trace("data 0\r\n0.1 0.2 0.3\r\nch> ", 26, "data 0", pts, 128, err, sizeof err) == -1);

    /* more points than the caller has room for */
    len = make_reply(buf, sizeof buf, "data 0", true, idx, 101, true);
    CHECK(vna_parse_trace(buf, len, "data 0", pts, 100, err, sizeof err) == -1);
    CHECK_HAS(err, "more than");

    /* nan parses (vna_evaluate flags it as INVALID) */
    CHECK(vna_parse_trace("data 0\r\nnan 0.0\r\nch> ", 22, "data 0", pts, 128, err, sizeof err) == 1);
    CHECK(isnan(pts[0].re));
}

static bool cal(const char *reply, char *why, size_t why_size)
{
    return vna_cal_ok(reply, strlen(reply), why, why_size);
}

static void test_cal(void)
{
    char why[128];

    CHECK(cal("load open short thru isoln Es Er Et cal'ed\r\nch> ", why, sizeof why));
    CHECK(cal("cal\r\nload open short thru isoln Es Er Et cal'ed\r\nch> ", why, sizeof why));
    CHECK(cal("LOAD OPEN SHORT THRU ES ER ET CAL'ED\r\nch> ", why, sizeof why));    /* case-insensitive */

    CHECK(!cal("load open short thru isoln Es Er Et\r\nch> ", why, sizeof why));
    CHECK_HAS(why, "not applied");
    CHECK(!cal("load open short Es Er cal'ed\r\nch> ", why, sizeof why));
    CHECK_HAS(why, "thru");
    CHECK(!cal("load open short thru Et cal'ed\r\nch> ", why, sizeof why));
    CHECK_HAS(why, "S11");
    CHECK(!cal("\r\nch> ", why, sizeof why));
    CHECK_HAS(why, "no calibration");
    CHECK(!cal("cal\r\n\r\nch> ", why, sizeof why));
    CHECK_HAS(why, "no calibration");
    CHECK(!cal("usage: cal [load|open|short|thru|isoln|done|on|off|reset]\r\nch> ", why, sizeof why));
    CHECK_HAS(why, "unrecognised");
}

static void test_math(void)
{
    CHECK_NEAR(vna_s11_to_vswr(0.1, 0.0), 1.1 / 0.9, 1e-12);
    CHECK_NEAR(vna_s11_to_vswr(0.0, 0.0), 1.0, 1e-12);
    CHECK_NEAR(vna_s11_to_vswr(0.06, 0.08), 1.1 / 0.9, 1e-12);             /* |gamma| = 0.1 */
    CHECK_NEAR(vna_s11_to_vswr(0.6, 0.8), 99.9, 1e-12);                    /* |gamma| = 1: open/short */
    CHECK_NEAR(vna_s11_to_vswr(0.2308, 0.0), 1.6, 0.001);                  /* the 1.6:1 limit is ~12.7 dB RL */

    CHECK_NEAR(vna_s21_to_loss_db(0.95, 0.0), 0.4455, 1e-3);
    CHECK_NEAR(vna_s21_to_loss_db(1.0, 0.0), 0.0, 1e-12);
    CHECK_NEAR(vna_s21_to_loss_db(0.0, 0.0), 99.9, 1e-12);
    CHECK_NEAR(vna_s21_to_loss_db(1.2, 0.0), -1.5836, 1e-3);
}

static void test_evaluate(void)
{
    static vna_point_t a[101];
    static vna_point_t b[101];
    vna_report_t r;

    fill(a, 101, 0.10, 0.0);
    fill(b, 101, 0.95, 0.0);
    vna_evaluate(a, 101, b, 101, 1.60, 0.58, &r);
    CHECK(r.status == VNA_PASS && r.have_values);
    CHECK_NEAR(r.vswr, 1.2222, 1e-3);
    CHECK_NEAR(r.loss_db, 0.4455, 1e-3);

    fill(a, 101, 0.30, 0.0);                                               /* VSWR 1.86 */
    vna_evaluate(a, 101, b, 101, 1.60, 0.58, &r);
    CHECK(r.status == VNA_FAIL);

    fill(a, 101, 0.10, 0.0);
    fill(b, 101, 0.90, 0.0);                                               /* 0.92 dB */
    vna_evaluate(a, 101, b, 101, 1.60, 0.58, &r);
    CHECK(r.status == VNA_FAIL);
    vna_evaluate(a, 101, b, 101, 1.60, 1.00, &r);                          /* limits are configurable */
    CHECK(r.status == VNA_PASS);

    fill(b, 101, 0.95, 0.0);
    vna_evaluate(a, 101, b, 101, vna_s11_to_vswr(0.10, 0.0), 0.58, &r);   /* exactly on the limit passes */
    CHECK(r.status == VNA_PASS);

    fill(b, 101, 1.2, 0.0);                                                /* S21 gain */
    vna_evaluate(a, 101, b, 101, 1.60, 0.58, &r);
    CHECK(r.status == VNA_INVALID && r.have_values);
    CHECK_HAS(r.detail, "gain");

    fill(b, 101, NAN, 0.0);
    vna_evaluate(a, 101, b, 101, 1.60, 0.58, &r);
    CHECK(r.status == VNA_INVALID);
    CHECK_HAS(r.detail, "non-finite");

    fill(b, 101, 0.95, 0.0);
    vna_evaluate(a, 100, b, 101, 1.60, 0.58, &r);
    CHECK(r.status == VNA_ERROR && !r.have_values);
    CHECK_HAS(r.detail, "expected 101");

    for (int i = 0; i < 101; i++) {                                        /* which point counts as 1 GHz */
        a[i].re = i / 1000.0;
        a[i].im = 0.0;
    }
    fill(b, 101, 0.95, 0.0);
    vna_evaluate(a, 101, b, 101, 9.9, 9.9, &r);
    CHECK_NEAR(r.vswr, vna_s11_to_vswr(0.050, 0.0), 1e-12);
    CHECK(VNA_SWEEP_START_HZ + VNA_TARGET_IDX * ((VNA_SWEEP_STOP_HZ - VNA_SWEEP_START_HZ) / (VNA_SWEEP_POINTS - 1)) == 1000000000u);

    CHECK(strcmp(vna_status_name(VNA_PASS), "PASS") == 0);
    CHECK(strcmp(vna_status_name(VNA_INVALID), "INVALID") == 0);
}

int main(void)
{
    test_prompt();
    test_parse();
    test_cal();
    test_math();
    test_evaluate();
    printf("vna_core: %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
