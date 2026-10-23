/*
 * vna_core: NanoVNA shell protocol parsing and the cable pass/fail logic.
 *
 * Plain C with no ESP-IDF dependencies so it can be unit-tested on a host (see test/host).
 * It follows the same rules as the Python tester (../../../../test_lmr240.py).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 900-1100 MHz in 2 MHz steps puts point 50 of 101 exactly on 1.0 GHz */
#define VNA_SWEEP_START_HZ  900000000u
#define VNA_SWEEP_STOP_HZ   1100000000u
#define VNA_SWEEP_POINTS    101
#define VNA_TARGET_IDX      (VNA_SWEEP_POINTS / 2)

/* A "loss" below this is S21 gain: bad calibration or connection, not a great cable */
#define VNA_MIN_PLAUSIBLE_LOSS_DB  (-0.10)

typedef struct {
    double re;
    double im;
} vna_point_t;

typedef enum {
    VNA_PASS = 0,
    VNA_FAIL,
    VNA_INVALID,    /* the VNA answered, but the reading cannot be trusted */
    VNA_ERROR,      /* no, partial or malformed reply */
} vna_status_t;

typedef struct {
    vna_status_t status;
    bool         have_values;   /* vswr and loss_db are set (PASS, FAIL and some INVALID) */
    double       vswr;
    double       loss_db;
    char         detail[144];   /* why INVALID/ERROR, or a note such as "cal check skipped" */
} vna_report_t;

const char *vna_status_name(vna_status_t s);

/* True when the reply ends with the shell prompt "ch>" (trailing whitespace ignored). */
bool vna_reply_complete(const char *buf, size_t len);

/*
 * Parses the reply to "data 0" / "data 1": drops the echoed command and the prompt, then reads
 * "re im" pairs. Returns the number of points, or -1 (reason in err) for a malformed line or more
 * than max_points.
 */
int vna_parse_trace(const char *reply, size_t len, const char *cmd,
                    vna_point_t *out, size_t max_points, char *err, size_t err_size);

/*
 * Checks the reply to "cal". The S11 error terms (Es, Er), the S21 thru normalization (Et) and the
 * applied flag (cal'ed) must all be listed. detail receives the reason when this returns false.
 * Assumes the usual NanoVNA wording ("load open short thru isoln Es Er Et cal'ed").
 */
bool vna_cal_ok(const char *reply, size_t len, char *detail, size_t detail_size);

double vna_s11_to_vswr(double re, double im);
double vna_s21_to_loss_db(double re, double im);

/* Evaluates point VNA_TARGET_IDX of both traces against the limits. */
void vna_evaluate(const vna_point_t *s11, size_t n11, const vna_point_t *s21, size_t n21,
                  double max_vswr, double max_loss_db, vna_report_t *out);

#ifdef __cplusplus
}
#endif
