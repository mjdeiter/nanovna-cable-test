# nanovna-cable-test

Bench tester for coax cable assemblies using a NanoVNA. It measures VSWR (S11) and insertion loss (S21) at 1.0 GHz, shows PASS/FAIL and appends every result to a CSV log.

The original write-up (spec, scripts, flash command) is kept in [docs/NanoVNA_Cable_Testing_Automation_Scripts.md](docs/NanoVNA_Cable_Testing_Automation_Scripts.md).

## Status

| Part | State |
| --- | --- |
| `test_lmr240.py` | Working draft, hardened after review. Tested against simulated NanoVNA output only; **not yet run on real hardware.** |
| `firmware/` | ESP-IDF project for an ESP32-S3 acting as USB host, with results on a T-Display-S3 screen. Replaces the original Arduino sketch (a stub that showed PASS for every cable). Builds with ESP-IDF 5.5 and its logic is unit-tested on a host, but it has **not yet run on real hardware**. See [firmware/README.md](firmware/README.md). |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/python test_lmr240.py                            # auto-detects the NanoVNA
.venv/bin/python test_lmr240.py --port /dev/cu.usbmodem4001
.venv/bin/python test_lmr240.py --max-loss 0.45            # another cable type or length
```

Scan or type a serial number at the prompt; `q` quits.

Defaults are the traveler limits at 1.0 GHz: VSWR <= 1.60:1 (about 12.7 dB return loss) and insertion loss <= 0.58 dB. The loss limit depends on cable type and assembly length, so set `--max-loss` per assembly.

## Before testing

Calibrate the NanoVNA (open, short, load and thru) over a range that covers 900-1100 MHz and keep the calibration applied. The tester sweeps 900-1100 MHz in 101 points, so point 50 is exactly 1.0 GHz, and it will not evaluate a cable if it can't confirm the calibration (`--skip-cal-check` bypasses this and notes it in the log).

The calibration check reads the reply to the `cal` command and assumes the usual NanoVNA wording. If your firmware words it differently, the raw reply is printed with the INVALID result so the check can be adjusted.

## Log

Every scan appends one row to `cable_test_results.csv` next to the script (`--log` to change it).

| Status | Meaning |
| --- | --- |
| PASS / FAIL | Reading evaluated against the limits |
| INVALID | Reading not trustworthy: calibration not confirmed, S21 shows gain, or non-numeric data |
| ERROR | No, partial or malformed reply from the NanoVNA |

Columns: `Timestamp, Serial_Number, VSWR_1GHz, Loss_dB_1GHz, Status, Limit_VSWR, Limit_Loss_dB, Detail`. The first five match the original script's log.

## Changes from the original script

- Reads each reply up to the NanoVNA's `ch>` prompt and requires all 101 points (a truncated reply used to be measured at the wrong frequency without any error)
- Calibration gate, S21-gain sanity check, and INVALID/ERROR rows in the log (these failures used to leave no trace)
- Configurable limits, recorded in the log
- Log defaults to next to the script instead of the current directory
- Fixed the dead `ttyACM` port match
