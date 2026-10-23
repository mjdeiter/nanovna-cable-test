#!/usr/bin/env python3
"""Production cable tester (1.0 GHz) for a NanoVNA over USB serial.

Sweeps 900-1100 MHz (101 points, so point 50 is exactly 1.0 GHz), reads S11 and S21,
checks them against the limits for the cable type and assembly length (CABLE_PROFILES)
and appends every result (PASS, FAIL, INVALID or ERROR) to a CSV quality log.
"""
import argparse
import csv
import datetime
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

# ---------------------------------------------------------
# CABLE PROFILES (AT 1.0 GHz)
# The limits follow from the cable type and the assembly length:
#   max loss (dB) = loss_per_100ft_db / 100 * length_ft + connector_allowance_db
#   max VSWR      = max_vswr
# Pick them with --material / --length, or with 'm' / 'l' at the serial-number prompt.
# --max-vswr / --max-loss replace a calculated limit.
#
# loss_per_100ft_db       Manufacturer attenuation at 1.0 GHz. Belden lists it directly for RG-8X
#                         (9258) and RG-58 (8240). Times Microwave lists LMR-240 and LMR-400 at
#                         900 and 1500 MHz only, so those two are interpolated.
# connector_allowance_db  Fixed allowance for the connector pair, added at any length.
# max_vswr                VSWR limit.
#
# STARTING VALUES: the connector allowances and every VSWR limit except LMR-240's traveler
# value are placeholders. Confirm them against your own travelers before production use.
# ---------------------------------------------------------
CABLE_PROFILES = {
    "LMR-240": {"loss_per_100ft_db": 8.0, "connector_allowance_db": 0.18, "max_vswr": 1.60},
    "LMR-400": {"loss_per_100ft_db": 4.1, "connector_allowance_db": 0.20, "max_vswr": 1.60},
    "RG-8X": {"loss_per_100ft_db": 11.2, "connector_allowance_db": 0.30, "max_vswr": 1.60},
    "RG-58": {"loss_per_100ft_db": 14.5, "connector_allowance_db": 0.30, "max_vswr": 1.60},
}
DEFAULT_MATERIAL = "LMR-240"
# The old flat limit was the traveler's 0.58 dB, which doesn't record an assembly length.
# 5 ft of LMR-240 reproduces it (8.0 / 100 * 5 + 0.18), so the defaults behave as before.
DEFAULT_LENGTH_FT = 5.0
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
# New columns go at the end so the positions of the older ones don't move
CSV_HEADER = ["Timestamp", "Serial_Number", "VSWR_1GHz", "Loss_dB_1GHz", "Status",
              "Limit_VSWR", "Limit_Loss_dB", "Detail", "Material_ID", "Length_Ft"]
MIN_LOG_COLUMNS = 5    # the original script's log had the first five


class VnaError(Exception):
    """The NanoVNA did not answer properly (timeout, partial or malformed reply)."""


class InvalidReading(Exception):
    """The NanoVNA answered, but the reading can't be trusted (calibration, implausible values)."""

    def __init__(self, message, vswr=None, loss_db=None):
        super().__init__(message)
        self.vswr = vswr
        self.loss_db = loss_db


def normalize_material(name):
    """Returns the CABLE_PROFILES key that 'lmr-240', 'LMR240', 'rg8x' ... stand for.

    Case, hyphens and spaces don't matter. Raises ValueError for an unknown cable type.
    """
    token = re.sub(r"[^A-Z0-9]", "", str(name).upper())
    for key in CABLE_PROFILES:
        if token and token == re.sub(r"[^A-Z0-9]", "", key.upper()):
            return key
    raise ValueError(f"unknown cable type {str(name).strip()!r} (choose from {', '.join(CABLE_PROFILES)})")


def parse_length(value):
    """Returns an assembly length in feet as a float. Raises ValueError unless it is a positive number."""
    try:
        length = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{str(value).strip()!r} is not a number of feet") from None
    if not math.isfinite(length) or length <= 0:
        raise ValueError(f"length must be a positive number of feet, not {str(value).strip()!r}")
    return length


def calc_max_loss(material, length_ft):
    """Insertion loss limit (dB) at 1.0 GHz for an assembly of this cable type and length:
    (loss per 100 ft / 100) * length in ft + the fixed connector allowance."""
    profile = CABLE_PROFILES[normalize_material(material)]
    return (profile["loss_per_100ft_db"] / 100.0) * parse_length(length_ft) + profile["connector_allowance_db"]


def resolve_limits(material, length_ft, max_vswr=None, max_loss=None):
    """Returns (max_vswr, max_loss_db): the cable profile's limits unless explicit ones are given."""
    material = normalize_material(material)
    length_ft = parse_length(length_ft)
    if max_vswr is None:
        max_vswr = CABLE_PROFILES[material]["max_vswr"]
    if max_loss is None:
        max_loss = calc_max_loss(material, length_ft)
    return max_vswr, max_loss


class Setup:
    """The cable type and assembly length under test, and the limits that follow from them.

    --max-vswr / --max-loss replace the calculated limits for the assembly they were given
    for: choosing another material drops both, choosing another length drops the loss one.
    """

    def __init__(self, material=DEFAULT_MATERIAL, length_ft=DEFAULT_LENGTH_FT, max_vswr=None, max_loss=None):
        self.material = normalize_material(material)
        self.length_ft = parse_length(length_ft)
        self.vswr_override = max_vswr
        self.loss_override = max_loss

    @property
    def max_vswr(self):
        return resolve_limits(self.material, self.length_ft, self.vswr_override, self.loss_override)[0]

    @property
    def max_loss(self):
        return resolve_limits(self.material, self.length_ft, self.vswr_override, self.loss_override)[1]

    def set_material(self, material):
        """Switches the cable type. Returns the names of the overrides it dropped."""
        material = normalize_material(material)
        if material == self.material:
            return []
        self.material = material
        return self._drop_overrides(vswr=True, loss=True)

    def set_length(self, length_ft):
        """Switches the assembly length. Returns the names of the overrides it dropped."""
        length_ft = parse_length(length_ft)
        if length_ft == self.length_ft:
            return []
        self.length_ft = length_ft
        return self._drop_overrides(vswr=False, loss=True)

    def _drop_overrides(self, vswr, loss):
        dropped = []
        if vswr and self.vswr_override is not None:
            self.vswr_override = None
            dropped.append("--max-vswr")
        if loss and self.loss_override is not None:
            self.loss_override = None
            dropped.append("--max-loss")
        return dropped

    def describe(self):
        return (f"{self.material}, {self.length_ft:g} ft: "
                f"VSWR <= {self.max_vswr:.2f}:1, Loss <= {self.max_loss:.2f} dB")


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
    """Creates the CSV log with its header, or brings an older log up to the current columns.

    An older log (the leading columns of CSV_HEADER, at least the original five) is rewritten
    with the full header and its rows padded with empty cells, after a copy of it is saved next
    to it. A log with any other header is left alone. Raises OSError if the log can't be
    written or isn't recognised.
    """
    if not log_file.exists() or log_file.stat().st_size == 0:
        with open(log_file, "a", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)
        return

    with open(log_file, newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0] if rows else []
    if header == CSV_HEADER:
        return
    if not (MIN_LOG_COLUMNS <= len(header) < len(CSV_HEADER) and header == CSV_HEADER[:len(header)]):
        raise OSError(f"{log_file} has an unrecognised header {header}; "
                      "move it aside or choose another file with --log")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = log_file.with_name(f"{log_file.stem}.pre-upgrade-{stamp}{log_file.suffix}")
    shutil.copy2(log_file, backup)
    tmp = log_file.with_name(log_file.name + ".tmp")
    try:
        with open(tmp, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            for row in rows[1:]:
                if row:
                    writer.writerow(row + [""] * (len(CSV_HEADER) - len(row)))
        os.replace(tmp, log_file)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    print(f"[+] Added the {', '.join(CSV_HEADER[len(header):])} "
          f"column(s) to {log_file} (previous log saved as {backup.name})")


def log_result(log_file, serial_num, vswr, loss_db, status, detail, max_vswr, max_loss,
               material="", length_ft=None):
    """Appends one row to the CSV quality log. Returns False (loudly) if it can't be written."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [timestamp, serial_num,
           "" if vswr is None else f"{vswr:.2f}",
           "" if loss_db is None else f"{loss_db:.2f}",
           status, f"{max_vswr:.2f}", f"{max_loss:.2f}", detail,
           material, "" if length_ft is None else f"{length_ft:.2f}"]
    try:
        with open(log_file, "a", newline="") as f:
            csv.writer(f).writerow(row)
    except OSError as e:
        print(f"[!!] COULD NOT WRITE LOG {log_file}: {e}")
        print(f"[!!] Record this result by hand: {row}")
        return False
    print(f"[+] Saved to log file: {log_file}")
    return True


def run_test(ser, serial_num, log_file=DEFAULT_LOG, max_vswr=None, max_loss=None,
             check_cal=True, settle=0.6, material=DEFAULT_MATERIAL, length_ft=DEFAULT_LENGTH_FT):
    """Tests one cable and logs the outcome. Returns "PASS", "FAIL", "INVALID" or "ERROR".

    The limits come from the cable type and length (CABLE_PROFILES) unless max_vswr / max_loss
    are given.
    """
    material = normalize_material(material)
    length_ft = parse_length(length_ft)
    max_vswr, max_loss = resolve_limits(material, length_ft, max_vswr, max_loss)
    print(f"\n[*] Sweeping cable S/N: {serial_num}...")

    try:
        vswr, loss_db, detail = measure(ser, check_cal, settle)
    except InvalidReading as e:
        print(f"[!] INVALID READING: {e}")
        print("[!] Not evaluated against the limits. Fix the problem and re-test this cable.")
        log_result(log_file, serial_num, e.vswr, e.loss_db, "INVALID", str(e), max_vswr, max_loss,
                   material, length_ft)
        return "INVALID"
    except Exception as e:    # VnaError, serial/OS errors, anything unexpected: never lose a scan
        detail = str(e) if isinstance(e, (VnaError, OSError)) else f"unexpected {type(e).__name__}: {e}"
        print(f"[!] NanoVNA error: {detail}")
        print("[!] Not evaluated against the limits. Check the connection and re-test this cable.")
        log_result(log_file, serial_num, None, None, "ERROR", detail, max_vswr, max_loss,
                   material, length_ft)
        return "ERROR"

    # Evaluate against the limits for this cable type and length
    vswr_pass = vswr <= max_vswr
    loss_pass = loss_db <= max_loss
    overall_pass = vswr_pass and loss_pass
    status = "PASS" if overall_pass else "FAIL"

    # Print Bench Results
    print("---------------------------------------------------------")
    print(f" RESULT FOR S/N: {serial_num}")
    print(f" Cable         : {material}, {length_ft:g} ft")
    print(" Frequency     : 1.0 GHz")
    print(f" S11 VSWR      : {vswr:.2f}:1  [{'PASS' if vswr_pass else 'FAIL'}] (Limit: <= {max_vswr:.2f}:1)")
    print(f" S21 Loss      : {loss_db:.2f} dB [{'PASS' if loss_pass else 'FAIL'}] (Limit: <= {max_loss:.2f} dB)")
    print("---------------------------------------------------------")
    print(f" OVERALL STATUS : {'>>> PASS <<<' if overall_pass else '*** FAIL ***'}")
    if detail:
        print(f" NOTE           : {detail}")
    print("---------------------------------------------------------")

    # Append to CSV Quality Log
    log_result(log_file, serial_num, vswr, loss_db, status, detail, max_vswr, max_loss,
               material, length_ft)
    return status


def change_material(setup):
    """Asks which cable type to test next. Returns True if one was chosen.

    Blank or unrecognised input keeps the current type.
    """
    names = list(CABLE_PROFILES)
    width = max(len(name) for name in names)
    print("\nCable types:")
    for i, name in enumerate(names, 1):
        p = CABLE_PROFILES[name]
        print(f"  {i}) {name:<{width}} {p['loss_per_100ft_db']:>5.1f} dB/100 ft  "
              f"+{p['connector_allowance_db']:.2f} dB connectors  VSWR <= {p['max_vswr']:.2f}")
    reply = input(f"Cable type [{setup.material}] (number or name, Enter keeps it): ").strip()
    if not reply:
        print(f"[*] Keeping {setup.material}")
        return False
    if reply.isascii() and reply.isdigit() and 1 <= int(reply) <= len(names):
        choice = names[int(reply) - 1]
    else:
        try:
            choice = normalize_material(reply)
        except ValueError as e:
            print(f"[!] {e}; keeping {setup.material}")
            return False
    dropped = setup.set_material(choice)
    print(f"[+] Now testing {setup.describe()}")
    if dropped:
        print(f"[*] {', '.join(dropped)} cleared: the limits now come from the {setup.material} profile")
    return True


def change_length(setup):
    """Asks for the length of the assemblies to test next. Returns True if one was entered.

    Blank or invalid input keeps the current length.
    """
    reply = input(f"Assembly length in feet [{setup.length_ft:g}] (Enter keeps it): ").strip()
    if not reply:
        print(f"[*] Keeping {setup.length_ft:g} ft")
        return False
    try:
        length = parse_length(reply)
    except ValueError as e:
        print(f"[!] {e}; keeping {setup.length_ft:g} ft")
        return False
    dropped = setup.set_length(length)
    print(f"[+] Now testing {setup.describe()}")
    if dropped:
        print(f"[*] {', '.join(dropped)} cleared: the loss limit is now calculated from the length")
    return True


def batch_loop(ser, setup, log_file, check_cal=True, settle=0.6):
    """Tests cables one serial number at a time until 'q'.

    'm' changes the cable type and 'l' the assembly length for the cables that follow.
    """
    while True:
        sn = input(f"\n[{setup.material}, {setup.length_ft:g} ft] Scan/Type Cable Serial Number "
                   "('m' = cable type, 'l' = length, 'q' = quit): ").strip()
        command = sn.lower()
        if command in ("q", "exit"):
            break
        if not sn:
            continue
        if command == "m":
            change_material(setup)
        elif command == "l":
            change_length(setup)
        else:
            run_test(ser, sn, log_file, setup.max_vswr, setup.max_loss, check_cal, settle,
                     setup.material, setup.length_ft)


def material_arg(text):
    try:
        return normalize_material(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def length_arg(text):
    try:
        return parse_length(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="NanoVNA cable tester: VSWR (S11) and insertion loss (S21) at 1.0 GHz.")
    ap.add_argument("--port", help="serial port (default: auto-detect, else ask)")
    ap.add_argument("--material", type=material_arg, default=DEFAULT_MATERIAL, metavar="TYPE",
                    help=f"cable type: {', '.join(CABLE_PROFILES)} (default {DEFAULT_MATERIAL}); "
                         "type 'm' at the prompt to change it during a batch")
    ap.add_argument("--length", type=length_arg, default=DEFAULT_LENGTH_FT, metavar="FEET",
                    help=f"assembly length in feet (default {DEFAULT_LENGTH_FT:g}); sets the loss limit; "
                         "type 'l' at the prompt to change it during a batch")
    ap.add_argument("--max-vswr", type=float, default=None,
                    help="VSWR limit, replacing the one from the cable type")
    ap.add_argument("--max-loss", type=float, default=None,
                    help="insertion loss limit in dB, replacing the one calculated from "
                         "cable type and length")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG,
                    help=f"CSV log file (default: {DEFAULT_LOG})")
    ap.add_argument("--skip-cal-check", action="store_true",
                    help="don't verify NanoVNA calibration (logged as 'cal check skipped')")
    ap.add_argument("--settle", type=float, default=0.6,
                    help="seconds to wait after setting the sweep range (default 0.6)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    check_cal = not args.skip_cal_check
    setup = Setup(args.material, args.length, args.max_vswr, args.max_loss)

    print("=========================================================")
    print("          Production Cable Tester (1.0 GHz)              ")
    print("=========================================================")
    print(f" Cable : {setup.material}, {setup.length_ft:g} ft")
    print(f" Limits: VSWR <= {setup.max_vswr:.2f}:1, Loss <= {setup.max_loss:.2f} dB")
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
        batch_loop(ser, setup, args.log, check_cal, args.settle)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting batch tester.")
    except (VnaError, OSError) as e:
        print(f"[!] Startup failed: {e}")
        sys.exit(1)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
