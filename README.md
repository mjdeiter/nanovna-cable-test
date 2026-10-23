# nanovna-cable-test

Bench tester for coax cable assemblies using a NanoVNA. It measures VSWR (S11) and insertion loss (S21) at 1.0 GHz, shows PASS/FAIL and appends every result to a CSV log.

The original write-up (spec, scripts, flash command) is kept in [docs/NanoVNA_Cable_Testing_Automation_Scripts.md](docs/NanoVNA_Cable_Testing_Automation_Scripts.md).

## Status

| Part | State |
| --- | --- |
| `test_lmr240.py` | Working draft, hardened after review. Tested against simulated NanoVNA output only (see [Tests](#tests)); **not yet run on real hardware.** |
| `firmware/` | ESP-IDF project for an ESP32-S3 acting as USB host, with results on a T-Display-S3 screen. Replaces the original Arduino sketch (a stub that showed PASS for every cable). Builds with ESP-IDF 5.5 and its logic is unit-tested on a host, but it has **not yet run on real hardware**. See [firmware/README.md](firmware/README.md). |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/python test_lmr240.py                            # auto-detects the NanoVNA; LMR-240, 5 ft
.venv/bin/python test_lmr240.py --port /dev/cu.usbmodem4001
.venv/bin/python test_lmr240.py --material RG-58 --length 25
.venv/bin/python test_lmr240.py --max-loss 0.45            # fixed loss limit instead of the calculated one
```

Scan or type a serial number at the prompt; `q` quits. The prompt shows the cable type and length being tested. Type `m` to switch cable type or `l` to enter another length between cables; the limits are recalculated for the cables that follow. (A cable whose serial number is exactly `m` or `l` can't be scanned.)

## Cable types and limits

At 1.0 GHz, the limits come from the cable type and the assembly length:

- loss limit (dB) = (loss per 100 ft / 100) x length (ft) + connector allowance
- VSWR limit = the cable type's `max_vswr` (1.60:1 is about 12.7 dB return loss)

The profiles are in `CABLE_PROFILES` at the top of `test_lmr240.py`:

| Cable | Loss (dB / 100 ft) | Connector allowance (dB) | Max VSWR | Loss limit at 10 ft (dB) |
| --- | --- | --- | --- | --- |
| LMR-240 | 8.0 | 0.18 | 1.60 | 0.98 |
| LMR-400 | 4.1 | 0.20 | 1.60 | 0.61 |
| RG-8X | 11.2 | 0.30 | 1.60 | 1.42 |
| RG-58 | 14.5 | 0.30 | 1.60 | 1.75 |

The loss figures are manufacturer attenuation at 1.0 GHz: Belden lists it directly for RG-8X (9258) and RG-58 (8240); Times Microwave lists LMR-240 and LMR-400 at 900 and 1500 MHz only, so those two are interpolated. **The connector allowances and the VSWR limits other than LMR-240's are starting values, not traveler data: confirm them before production use.**

With no `--material` or `--length`, the tester uses 5 ft of LMR-240, which gives the 0.58 dB limit used before profiles existed (the traveler doesn't record its assembly length, so check that 5 ft matches yours). `--max-vswr` and `--max-loss` replace a calculated limit for the assembly they were given for: `m` drops both, `l` drops the loss limit.

The ESP32 firmware in `firmware/` still takes flat limits from menuconfig; the profiles apply to the Python tester only.

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

Columns: `Timestamp, Serial_Number, VSWR_1GHz, Loss_dB_1GHz, Status, Limit_VSWR, Limit_Loss_dB, Detail, Material_ID, Length_Ft`. The first five match the original script's log. `Material_ID` and `Length_Ft` record the cable type and length each cable was tested as. A log from an earlier version is upgraded when the tester starts: the new columns are added (empty for old rows) and the previous file is kept next to it as `*.pre-upgrade-<timestamp>.csv`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The suite drives the tester against a simulated NanoVNA, so it needs no hardware. It covers the cable profiles and loss calculation, the command-line flags, the `m` / `l` prompt commands and the CSV log, including upgrading an older log.

## Changes from the original script

- Reads each reply up to the NanoVNA's `ch>` prompt and requires all 101 points (a truncated reply used to be measured at the wrong frequency without any error)
- Calibration gate, S21-gain sanity check, and INVALID/ERROR rows in the log (these failures used to leave no trace)
- Configurable limits, recorded in the log
- Cable profiles (LMR-240, LMR-400, RG-8X, RG-58): the loss limit is calculated from cable type and length, set with `--material` / `--length` or `m` / `l` at the prompt
- Log records `Material_ID` and `Length_Ft`
- Log defaults to next to the script instead of the current directory
- Fixed the dead `ttyACM` port match
