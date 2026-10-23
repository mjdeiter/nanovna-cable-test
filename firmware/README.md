# ESP32-S3 firmware

Standalone version of the cable tester. An ESP32-S3 is the USB host and a NanoVNA-H/H4 is the device: press the button and the firmware sweeps 900-1100 MHz and shows PASS/FAIL for VSWR and insertion loss at 1.0 GHz on a LilyGO T-Display-S3. Every result is also logged on the UART console.

It replaces the original Arduino sketch, which hard-coded its results (VSWR 1.35, loss 0.42) and so showed PASS for every cable.

## Status

| What | State |
| --- | --- |
| Build | Clean with ESP-IDF v5.5.6 (esp32s3), no warnings. Binary about 307 KB, 70% of the 1 MB app partition free. |
| Logic | Protocol parsing, calibration gate, pass/fail rules and the measurement sequence are unit-tested on a host: 121 checks (`make -C test/host test`). |
| Hardware | **Not yet run on real hardware.** Untested: USB host enumeration of the NanoVNA, the calibration check (it assumes the usual `cal` reply wording) and the T-Display-S3 display bring-up (pins, color inversion and panel offsets come from the LilyGO pin map and can be adjusted in menuconfig). |

## Hardware notes

- The NanoVNA connects to the ESP32-S3's native USB pins (GPIO19/20), which the S3 drives as a USB host.
- **The S3 does not supply VBUS.** The NanoVNA needs 5 V on the connector's VBUS, for example from a powered OTG adapter or Y cable. The T-Display-S3's only USB-C port is also its power and programming port.
- With the USB pins used as the host port, the console is on **UART0 (GPIO43 TX, GPIO44 RX)**. To reflash over USB, put the board in download mode (hold BOOT while resetting).

## Build

ESP-IDF 5.5 (the version this was built and checked with):

```bash
idf.py set-target esp32s3
idf.py build
idf.py -p PORT flash monitor
```

Or with Docker, without installing ESP-IDF:

```bash
mkdir -p /tmp/idf_mc
docker run --rm -v "$PWD":/project -v /tmp/idf_mc:/opt/esp/root_managed_components \
  -w /project -u "$(id -u):$(id -g)" -e HOME=/tmp \
  espressif/idf:release-v5.5 bash -c 'idf.py set-target esp32s3 && idf.py build'
```

`idf.py menuconfig`, menu "NanoVNA cable tester": limits (default VSWR 1.60, loss 0.58 dB), USB VID/PID (default 0483:5740, the STM32 virtual COM port of NanoVNA-H/H4), test button GPIO (default GPIO0, the BOOT button), settle time, skipping the calibration check, and the display orientation and offsets.

The USB CDC-ACM host driver is the `espressif/usb_host_cdc_acm` component, fetched by the ESP-IDF component manager (locked to 2.4.1 in `dependencies.lock`).

`platformio.ini` is included but unverified: on the machine used here, `pio run` stops before compiling with `No module named 'SCons.Tool.FortranCommon'`, which is a problem in that PlatformIO install rather than in this project.

## How it works

| File | Role |
| --- | --- |
| `main/vna_usb.c` | Runs the ESP-IDF USB host library and CDC-ACM host driver, reconnects when the NanoVNA is plugged in, and sends shell commands, reading each reply up to the `ch>` prompt. A timeout or partial reply is an error. |
| `main/vna_test.c` | The measurement: `sweep 900000000 1100000000 101`, settle, `cal` (must show Es, Er, Et and cal'ed), `data 0` (S11), `data 1` (S21). All 101 points are required; point 50 is 1.0 GHz. |
| `components/vna_core/` | Plain C parsing and evaluation with no ESP-IDF dependencies, following the same rules as the Python tester: PASS, FAIL, INVALID (reading not trustworthy) or ERROR (no, partial or malformed reply). |
| `main/display.c`, `main/fbtext.c`, `main/lcd_tdisplay_s3.c` | 5x7 text rendered into an RGB565 framebuffer and pushed to the ST7789 over the 8-bit parallel bus. |
| `main/main.c` | Button, connect/sync handling and the screens. |

The screen is green for PASS, red for FAIL, amber for INVALID and purple for ERROR. INVALID and ERROR show the reason.

## Tests

```bash
make -C test/host test        # protocol, pass/fail rules, measurement sequence, font renderer
make -C test/host art         # prints the font as ASCII art
```
