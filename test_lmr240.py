#!/usr/bin/env python3
"""LMR-240 production cable tester (1.0 GHz) for a NanoVNA over USB serial.

Sweeps 900-1100 MHz (101 points, so point 50 is exactly 1.0 GHz), reads S11 and S21,
checks them against the traveler limits and appends every result (PASS, FAIL,
INVALID or ERROR) to a CSV quality log.
"""
import argparse
import csv
import datetime
import math
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

# ---------------------------------------------------------
# TRAVELER SPECIFICATION LIMITS (AT 1.0 GHz)
# Defaults only: override per assembly with --max-vswr / --max-loss
# ---------------------------------------------------------
MAX_VSWR = 1.60          # Traveler: VSWR <= 1.6:1
MAX_LOSS_DB = 0.58       # Traveler: Insertion Loss <= 0.58 dB
TARGET_FREQ_HZ = 1e9     # Target Test Frequency: 1.0 GHz

# 900-1100 MHz in 2 MHz steps puts point 50 of 101 exactly on 1.0 GHz
SWEEP_START_HZ = 900_000_000
SWEEP_STOP_HZ = 1_100_000_000
SWEEP_POINTS = 101
TARGET_IDX = SWEEP_POINTS // 2
assert SWEEP_START_HZ + TARGET_IDX * (SWEEP_STOP_HZ - SWEEP_START_HZ) / (SWEEP_POINTS - 1) == TARGET_FREQ_HZ

# A "loss" below this is S21 gain: bad calibration or connection, not a great cable
MIN_PLAUSIBLE_LOSS_DB = -0.10

PROMPT = "ch>"
DEFAULT_LOG = Path(__file__).resolve().parent / "cable_test_results.csv"
CSV_HEADER = ["Timestamp", "Serial_Number", "VSWR_1GHz", "Loss_dB_1GHz", "Status",
              "Limit_VSWR", "Limit_Loss_dB", "Detail"]


class VnaError(Exception):
    """The NanoVNA did not answer properly (timeout, partial or malformed reply)."""


class InvalidReading(Exception):
    """The NanoVNA answered, but the reading can't be trusted (calibration, implausible values)."""

    def __init__(self, message, vswr=None, loss_db=None):
        super().__init__(message)
        self.vswr = vswr
        self.loss_db = loss_db


def find_nanovna():
    """Detects connected NanoVNA USB serial port."""
    for p in list_ports.comports():
        if "0483" in p.hwid.lower() or "usbmodem" in p.device.lower() or "ttyacm" in p.device.lower():
            return p.device
    return None


def read_until_prompt(ser, timeout):
    """Reads until the NanoVNA shell prompt ('ch>') arrives or the timeout expires."""
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            if buf.rstrip().endswith(PROMPT.encode("ascii")):
                break
    return buf.decode("utf-8", errors="ignore")


def send_cmd(ser, cmd, timeout=3.0):
    """Sends one NanoVNA shell command and returns its reply lines (echo and prompt removed).

    Reads until the 'ch>' prompt instead of sleeping for a fixed time, so a slow or
    partial reply raises VnaError instead of being parsed as if it were complete.
    (The NanoVNA shell is its own text protocol, not SCPI.)
    """
    ser.reset_input_buffer()
    ser.write(f"{cmd}\r\n".encode("ascii"))
    text = read_until_prompt(ser, timeout)
    if not text.rstrip().endswith(PROMPT):
        raise VnaError(f"no '{PROMPT}' prompt after '{cmd}' (timeout or partial reply)")
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    if lines and lines[0].endswith(cmd):    # drop the echoed command
        lines = lines[1:]
    return [ln for ln in lines if not ln.startswith(PROMPT)]


def sync_prompt(ser):
    """Wakes the NanoVNA shell and confirms it answers with its 'ch>' prompt."""
    ser.reset_input_buffer()
    ser.write(b"\r\n")
    if not read_until_prompt(ser, 2.0).rstrip().endswith(PROMPT):
        raise VnaError(f"no '{PROMPT}' prompt: is this port really a NanoVNA?")


def read_trace(ser, channel):
    """Reads one trace ('data 0' = S11, 'data 1' = S21) as a list of (re, im) tuples."""
    points = []
    for ln in send_cmd(ser, f"data {channel}", timeout=4.0):
        parts = ln.split()
        if len(parts) != 2:
            raise VnaError(f"malformed line in 'data {channel}': {ln!r}")
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            raise VnaError(f"non-numeric line in 'data {channel}': {ln!r}") from None
    if len(points) != SWEEP_POINTS:
        raise VnaError(f"'data {channel}' returned {len(points)} points, expected {SWEEP_POINTS}")
    return points


def check_calibration(ser):
    """Returns (ok, detail) based on the NanoVNA 'cal' command.

    With no arguments, NanoVNA firmware lists the calibration items it holds, e.g.
    "load open short thru isoln Es Er Et cal'ed". We need the S11 error terms (Es, Er),
    the S21 thru normalization (Et) and the applied flag (cal'ed).
    Assumes that wording; NOT yet verified on real hardware. The raw reply is included
    in the failure detail so the check can be adjusted if a firmware words it differently.
    """
    try:
        reply = " ".join(send_cmd(ser, "cal"))
    except VnaError as e:
        return False, f"could not read calibration state ({e})"
    if not reply:
        return False, "no calibration stored (empty reply to 'cal')"
    tokens = {t.lower() for t in reply.split()}
    if not tokens & {"load", "open", "short", "thru", "isoln", "es", "er", "et", "cal'ed"}:
        return False, f"unrecognised reply to 'cal': {reply!r}"
    problems = []
    if "cal'ed" not in tokens:
        problems.append("calibration not applied")
    if not {"es", "er"} <= tokens:
        problems.append("S11 error terms missing")
    if "et" not in tokens:
        problems.append("S21 thru normalization missing")
    if problems:
        return False, f"{'; '.join(problems)} (cal reply: {reply!r})"
    return True, "ok"


def s11_to_vswr(re, im):
    """Converts complex S11 parameters to VSWR."""
    gamma = math.sqrt(re**2 + im**2)
    if gamma >= 0.999:
        return 99.9
    return (1.0 + gamma) / (1.0 - gamma)


def s21_to_loss_db(re, im):
    """Converts complex S21 parameters to Insertion Loss in dB."""
    mag = math.sqrt(re**2 + im**2)
    if mag <= 0.00001:
        return 99.9
    return -20.0 * math.log10(mag)


def measure(ser, check_cal=True, settle=0.6):
    """Runs one sweep and returns (vswr, loss_db, detail) at 1.0 GHz.

    Raises VnaError if a reply can't be trusted as complete, and InvalidReading if
    replies arrived intact but the numbers can't be used.
    """
    # Sweep range around 1 GHz (900 MHz to 1100 MHz, 101 points)
    send_cmd(ser, f"sweep {SWEEP_START_HZ} {SWEEP_STOP_HZ} {SWEEP_POINTS}")
    time.sleep(settle)    # let at least one full sweep finish at the new range

    # Check calibration after the sweep command: changing the range can switch it off
    detail = "cal check skipped"
    if check_cal:
        cal_ok, cal_detail = check_calibration(ser)
        if not cal_ok:
            raise InvalidReading(f"calibration not confirmed: {cal_detail}")
        detail = ""

    # Fetch raw data arrays (data 0 = S11, data 1 = S21)
    s11 = read_trace(ser, 0)
    s21 = read_trace(ser, 1)

    vswr = s11_to_vswr(*s11[TARGET_IDX])
    loss_db = s21_to_loss_db(*s21[TARGET_IDX])

    if not (math.isfinite(vswr) and math.isfinite(loss_db)):
        raise InvalidReading("non-finite value in the measurement", vswr, loss_db)
    if loss_db < MIN_PLAUSIBLE_LOSS_DB:
        raise InvalidReading(f"S21 shows {-loss_db:.2f} dB of gain: bad calibration or connection",
                             vswr, loss_db)
    return vswr, loss_db, detail


def ensure_log(log_file):
    """Creates the CSV log with its header if needed. Raises OSError if it can't be written."""
    if not log_file.exists() or log_file.stat().st_size == 0:
        with open(log_file, "a", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)


def log_result(log_file, serial_num, vswr, loss_db, status, detail, max_vswr, max_loss):
    """Appends one row to the CSV quality log. Returns False (loudly) if it can't be written."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [timestamp, serial_num,
           "" if vswr is None else f"{vswr:.2f}",
           "" if loss_db is None else f"{loss_db:.2f}",
           status, f"{max_vswr:.2f}", f"{max_loss:.2f}", detail]
    try:
        with open(log_file, "a", newline="") as f:
            csv.writer(f).writerow(row)
    except OSError as e:
        print(f"[!!] COULD NOT WRITE LOG {log_file}: {e}")
        print(f"[!!] Record this result by hand: {row}")
        return False
    print(f"[+] Saved to log file: {log_file}")
    return True


def run_test(ser, serial_num, log_file=DEFAULT_LOG, max_vswr=MAX_VSWR, max_loss=MAX_LOSS_DB,
             check_cal=True, settle=0.6):
    """Tests one cable and logs the outcome. Returns "PASS", "FAIL", "INVALID" or "ERROR"."""
    print(f"\n[*] Sweeping cable S/N: {serial_num}...")

    try:
        vswr, loss_db, detail = measure(ser, check_cal, settle)
    except InvalidReading as e:
        print(f"[!] INVALID READING: {e}")
        print("[!] Not evaluated against the limits. Fix the problem and re-test this cable.")
        log_result(log_file, serial_num, e.vswr, e.loss_db, "INVALID", str(e), max_vswr, max_loss)
        return "INVALID"
    except Exception as e:    # VnaError, serial/OS errors, anything unexpected: never lose a scan
        detail = str(e) if isinstance(e, (VnaError, OSError)) else f"unexpected {type(e).__name__}: {e}"
        print(f"[!] NanoVNA error: {detail}")
        print("[!] Not evaluated against the limits. Check the connection and re-test this cable.")
        log_result(log_file, serial_num, None, None, "ERROR", detail, max_vswr, max_loss)
        return "ERROR"

    # Evaluate against Traveler limits
    vswr_pass = vswr <= max_vswr
    loss_pass = loss_db <= max_loss
    overall_pass = vswr_pass and loss_pass
    status = "PASS" if overall_pass else "FAIL"

    # Print Bench Results
    print("---------------------------------------------------------")
    print(f" RESULT FOR S/N: {serial_num}")
    print(" Frequency     : 1.0 GHz")
    print(f" S11 VSWR      : {vswr:.2f}:1  [{'PASS' if vswr_pass else 'FAIL'}] (Limit: <= {max_vswr:.2f}:1)")
    print(f" S21 Loss      : {loss_db:.2f} dB [{'PASS' if loss_pass else 'FAIL'}] (Limit: <= {max_loss:.2f} dB)")
    print("---------------------------------------------------------")
    print(f" OVERALL STATUS : {'>>> PASS <<<' if overall_pass else '*** FAIL ***'}")
    if detail:
        print(f" NOTE           : {detail}")
    print("---------------------------------------------------------")

    # Append to CSV Quality Log
    log_result(log_file, serial_num, vswr, loss_db, status, detail, max_vswr, max_loss)
    return status


def parse_args():
    ap = argparse.ArgumentParser(
        description="NanoVNA cable tester: VSWR (S11) and insertion loss (S21) at 1.0 GHz.")
    ap.add_argument("--port", help="serial port (default: auto-detect, else ask)")
    ap.add_argument("--max-vswr", type=float, default=MAX_VSWR,
                    help=f"VSWR limit (default {MAX_VSWR:.2f})")
    ap.add_argument("--max-loss", type=float, default=MAX_LOSS_DB,
                    help=f"insertion loss limit in dB (default {MAX_LOSS_DB:.2f}); "
                         "depends on cable type and assembly length")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG,
                    help=f"CSV log file (default: {DEFAULT_LOG})")
    ap.add_argument("--skip-cal-check", action="store_true",
                    help="don't verify NanoVNA calibration (logged as 'cal check skipped')")
    ap.add_argument("--settle", type=float, default=0.6,
                    help="seconds to wait after setting the sweep range (default 0.6)")
    return ap.parse_args()


def main():
    args = parse_args()
    check_cal = not args.skip_cal_check

    print("=========================================================")
    print("      LMR-240 Production Cable Tester (1.0 GHz)          ")
    print("=========================================================")
    print(f" Limits: VSWR <= {args.max_vswr:.2f}:1, Loss <= {args.max_loss:.2f} dB")
    if not check_cal:
        print(" WARNING: calibration check is OFF")

    port = args.port or find_nanovna() or input("Enter Serial Port: ").strip()
    try:
        ser = serial.Serial(port, 115200, timeout=0.25)
    except Exception as e:
        print(f"[!] Could not open port: {e}")
        sys.exit(1)

    try:
        ensure_log(args.log)
        sync_prompt(ser)
        print(f"[+] Connected to NanoVNA on {port}")
        print(f"[+] Logging to {args.log}\n")

        while True:
            sn = input("\nScan/Type Cable Serial Number (or 'q' to quit): ").strip()
            if sn.lower() in ['q', 'exit']:
                break
            if not sn:
                continue
            run_test(ser, sn, args.log, args.max_vswr, args.max_loss, check_cal, args.settle)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting batch tester.")
    except (VnaError, OSError) as e:
        print(f"[!] Startup failed: {e}")
        sys.exit(1)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
