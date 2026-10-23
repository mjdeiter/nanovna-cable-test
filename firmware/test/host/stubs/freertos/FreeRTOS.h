#pragma once

typedef unsigned int TickType_t;

#define pdMS_TO_TICKS(ms)  ((TickType_t)(ms))      /* 1 tick = 1 ms in the host tests */
#define portMAX_DELAY      0xffffffffu
