#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "vna_core.h"

/*
 * Runs one complete measurement: sweep 900-1100 MHz, check calibration, read S11 and S21 and
 * evaluate the 1.0 GHz point. Problems are reported in rep (status ERROR or INVALID), never lost.
 */
void vna_test_run(double max_vswr, double max_loss_db, bool check_cal, uint32_t settle_ms, vna_report_t *rep);
