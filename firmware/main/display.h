#pragma once

#include "vna_core.h"

void display_init(void);
void display_banner(const char *line1, const char *line2, const char *line3);
void display_ready(double max_vswr, double max_loss_db);
void display_report(const vna_report_t *r, double max_vswr, double max_loss_db);
