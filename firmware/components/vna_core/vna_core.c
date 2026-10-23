#include "vna_core.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PROMPT      "ch>"
#define PROMPT_LEN  (sizeof(PROMPT) - 1)

const char *vna_status_name(vna_status_t s)
{
    switch (s) {
    case VNA_PASS:    return "PASS";
    case VNA_FAIL:    return "FAIL";
    case VNA_INVALID: return "INVALID";
    case VNA_ERROR:   return "ERROR";
    }
    return "?";
}

bool vna_reply_complete(const char *buf, size_t len)
{
    while (len > 0 && isspace((unsigned char)buf[len - 1])) {
        len--;
    }
    return len >= PROMPT_LEN && memcmp(buf + len - PROMPT_LEN, PROMPT, PROMPT_LEN) == 0;
}

/* ---- line iteration: skips blank lines, prompts and the echoed command ---- */

typedef struct {
    const char *p;
    const char *end;
    const char *cmd;     /* command whose echo may be the first line (NULL or "" = no echo check) */
    bool        first;   /* next candidate line is the first non-prompt line */
} lines_t;

static void lines_init(lines_t *it, const char *reply, size_t len, const char *cmd)
{
    it->p = reply;
    it->end = reply + len;
    it->cmd = cmd;
    it->first = true;
}

static bool lines_next(lines_t *it, char *line, size_t line_size)
{
    while (it->p < it->end) {
        const char *nl = memchr(it->p, '\n', (size_t)(it->end - it->p));
        const char *stop = nl ? nl : it->end;
        const char *s = it->p;
        it->p = nl ? nl + 1 : it->end;

        while (s < stop && isspace((unsigned char)*s)) {
            s++;
        }
        while (stop > s && isspace((unsigned char)stop[-1])) {
            stop--;
        }
        size_t n = (size_t)(stop - s);
        if (n == 0) {
            continue;
        }
        if (n >= PROMPT_LEN && memcmp(s, PROMPT, PROMPT_LEN) == 0) {
            continue;                                   /* shell prompt */
        }
        if (it->first) {
            it->first = false;
            size_t cmd_len = it->cmd ? strlen(it->cmd) : 0;
            if (cmd_len > 0 && n >= cmd_len && memcmp(stop - cmd_len, it->cmd, cmd_len) == 0) {
                continue;                               /* echoed command */
            }
        }
        if (n >= line_size) {
            n = line_size - 1;
        }
        memcpy(line, s, n);
        line[n] = '\0';
        return true;
    }
    return false;
}

/* ---- data traces ---- */

static bool parse_pair(const char *line, double *re, double *im)
{
    char *p1 = NULL;
    char *p2 = NULL;

    *re = strtod(line, &p1);
    if (p1 == line) {
        return false;
    }
    *im = strtod(p1, &p2);
    if (p2 == p1) {
        return false;
    }
    while (isspace((unsigned char)*p2)) {
        p2++;
    }
    return *p2 == '\0';
}

int vna_parse_trace(const char *reply, size_t len, const char *cmd,
                    vna_point_t *out, size_t max_points, char *err, size_t err_size)
{
    lines_t it;
    char line[128];
    size_t n = 0;

    lines_init(&it, reply, len, cmd);
    while (lines_next(&it, line, sizeof line)) {
        double re;
        double im;

        if (!parse_pair(line, &re, &im)) {
            snprintf(err, err_size, "malformed line in '%s': %.40s", cmd, line);
            return -1;
        }
        if (n >= max_points) {
            snprintf(err, err_size, "'%s' returned more than %zu points", cmd, max_points);
            return -1;
        }
        out[n].re = re;
        out[n].im = im;
        n++;
    }
    return (int)n;
}

/* ---- calibration ---- */

/* name must be lower case */
static bool has_token(const char *s, const char *name)
{
    size_t name_len = strlen(name);

    while (*s) {
        while (*s && isspace((unsigned char)*s)) {
            s++;
        }
        const char *t = s;
        while (*s && !isspace((unsigned char)*s)) {
            s++;
        }
        size_t n = (size_t)(s - t);
        if (n == name_len) {
            size_t i = 0;
            while (i < n && tolower((unsigned char)t[i]) == name[i]) {
                i++;
            }
            if (i == n) {
                return true;
            }
        }
    }
    return false;
}

static void append(char *dst, size_t size, const char *sep, const char *text)
{
    size_t used = strlen(dst);

    if (used < size) {
        snprintf(dst + used, size - used, "%s%s", used ? sep : "", text);
    }
}

bool vna_cal_ok(const char *reply, size_t len, char *detail, size_t detail_size)
{
    static const char *const known[] = {"load", "open", "short", "thru", "isoln", "es", "er", "et", "cal'ed"};
    lines_t it;
    char line[160];
    char all[200];
    char problems[96];
    bool any = false;

    all[0] = '\0';
    lines_init(&it, reply, len, "cal");
    while (lines_next(&it, line, sizeof line)) {
        append(all, sizeof all, " ", line);
    }
    if (all[0] == '\0') {
        snprintf(detail, detail_size, "no calibration stored (empty reply to 'cal')");
        return false;
    }
    for (size_t i = 0; i < sizeof known / sizeof known[0]; i++) {
        any = any || has_token(all, known[i]);
    }
    if (!any) {
        snprintf(detail, detail_size, "unrecognised reply to 'cal': %.60s", all);
        return false;
    }

    problems[0] = '\0';
    if (!has_token(all, "cal'ed")) {
        append(problems, sizeof problems, "; ", "calibration not applied");
    }
    if (!has_token(all, "es") || !has_token(all, "er")) {
        append(problems, sizeof problems, "; ", "S11 error terms missing");
    }
    if (!has_token(all, "et")) {
        append(problems, sizeof problems, "; ", "S21 thru normalization missing");
    }
    if (problems[0] != '\0') {
        snprintf(detail, detail_size, "%s (cal reply: %.40s)", problems, all);
        return false;
    }
    snprintf(detail, detail_size, "ok");
    return true;
}

/* ---- numbers ---- */

double vna_s11_to_vswr(double re, double im)
{
    double gamma = sqrt(re * re + im * im);

    if (gamma >= 0.999) {
        return 99.9;
    }
    return (1.0 + gamma) / (1.0 - gamma);
}

double vna_s21_to_loss_db(double re, double im)
{
    double mag = sqrt(re * re + im * im);

    if (mag <= 0.00001) {
        return 99.9;
    }
    return -20.0 * log10(mag);
}

void vna_evaluate(const vna_point_t *s11, size_t n11, const vna_point_t *s21, size_t n21,
                  double max_vswr, double max_loss_db, vna_report_t *out)
{
    memset(out, 0, sizeof *out);

    if (n11 != VNA_SWEEP_POINTS || n21 != VNA_SWEEP_POINTS) {
        out->status = VNA_ERROR;
        snprintf(out->detail, sizeof out->detail, "expected %d points, got %zu (S11) and %zu (S21)",
                 VNA_SWEEP_POINTS, n11, n21);
        return;
    }

    out->vswr = vna_s11_to_vswr(s11[VNA_TARGET_IDX].re, s11[VNA_TARGET_IDX].im);
    out->loss_db = vna_s21_to_loss_db(s21[VNA_TARGET_IDX].re, s21[VNA_TARGET_IDX].im);
    out->have_values = true;

    if (!isfinite(out->vswr) || !isfinite(out->loss_db)) {
        out->status = VNA_INVALID;
        snprintf(out->detail, sizeof out->detail, "non-finite value in the measurement");
        return;
    }
    if (out->loss_db < VNA_MIN_PLAUSIBLE_LOSS_DB) {
        out->status = VNA_INVALID;
        snprintf(out->detail, sizeof out->detail, "S21 shows %.2f dB of gain: bad calibration or connection",
                 -out->loss_db);
        return;
    }
    out->status = (out->vswr <= max_vswr && out->loss_db <= max_loss_db) ? VNA_PASS : VNA_FAIL;
}
