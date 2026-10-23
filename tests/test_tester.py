"""Simulation tests for test_lmr240: cable profiles, length-based loss limits, the command line,
the 'm' / 'l' prompt commands and the CSV log, run against a simulated NanoVNA (no hardware).

Run from the project root:
    python -m unittest discover -s tests -v
"""
import contextlib
import csv
import io
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_lmr240 as t  # noqa: E402

REAL_MATERIALS = tuple(t.CABLE_PROFILES)    # captured before any test adds profiles of its own

# Made-up cables with round numbers, so expected values can be worked out by hand
TEST_PROFILES = {
    "TEST-CABLE": {"loss_per_100ft_db": 10.0, "connector_allowance_db": 0.25, "max_vswr": 1.40},
    "TEST-TIGHT": {"loss_per_100ft_db": 10.0, "connector_allowance_db": 0.25, "max_vswr": 1.30},
    "TEST-LOOSE": {"loss_per_100ft_db": 10.0, "connector_allowance_db": 0.25, "max_vswr": 1.90},
}
GOOD_CAL = "load open short thru isoln Es Er Et cal'ed"
LEGACY_HEADER = ["Timestamp", "Serial_Number", "VSWR_1GHz", "Loss_dB_1GHz", "Status",
                 "Limit_VSWR", "Limit_Loss_dB", "Detail"]


class FakeNanoVNA:
    """A NanoVNA shell on a fake serial port: echoes each command, answers it, then shows 'ch> '.

    S11 and S21 are flat except at the 1.0 GHz point, where they hold the requested VSWR and
    loss, so a tester that reads any other point gets an obviously wrong answer.
    """

    def __init__(self, vswr=1.20, loss_db=0.40, cal_reply=GOOD_CAL, points=t.SWEEP_POINTS):
        self.vswr = vswr
        self.loss_db = loss_db
        self.cal_reply = cal_reply
        self.points = points
        self.commands = []
        self.closed = False
        self._out = bytearray()

    @property
    def in_waiting(self):
        return len(self._out)

    def reset_input_buffer(self):
        self._out.clear()

    def read(self, size=1):
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def write(self, data):
        cmd = data.decode("ascii").strip()
        self.commands.append(cmd)
        reply = "".join(f"{line}\r\n" for line in self._answer(cmd))
        self._out += f"{cmd}\r\n{reply}ch> ".encode("ascii")
        return len(data)

    def close(self):
        self.closed = True

    def _answer(self, cmd):
        if cmd == "cal":
            return [self.cal_reply]
        if cmd in ("data 0", "data 1"):
            return self._trace(int(cmd[-1]))
        return []

    def _trace(self, channel):
        if channel == 0:    # S11: the reflection coefficient that gives the requested VSWR
            flat, at_target = 0.50, (self.vswr - 1.0) / (self.vswr + 1.0)
        else:               # S21: the magnitude that gives the requested loss
            flat, at_target = 0.10, 10 ** (-self.loss_db / 20.0)
        values = [flat] * t.SWEEP_POINTS
        values[t.TARGET_IDX] = at_target
        return [f"{v:.6f} 0.000000" for v in values[:self.points]]


class ScriptedInput:
    """Stands in for input(): hands out the scripted answers in order and records the prompts."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"the tester asked for more input than the test scripted: {prompt!r}")
        return self.answers.pop(0)


@contextlib.contextmanager
def quiet():
    """Captures the tester's console output so the test run stays readable."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


class TesterCase(unittest.TestCase):
    """A temporary log file plus the made-up TEST-* cable profiles."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.log = self.dir / "results.csv"
        profiles = mock.patch.dict(t.CABLE_PROFILES, TEST_PROFILES)
        profiles.start()
        self.addCleanup(profiles.stop)

    def scan(self, fake, material, length_ft, sn="SN-1", **kwargs):
        """Tests one cable on the simulated NanoVNA and returns its status."""
        with quiet():
            t.ensure_log(self.log)
            return t.run_test(fake, sn, self.log, settle=0, material=material, length_ft=length_ft, **kwargs)

    def rows(self):
        with open(self.log, newline="") as f:
            return list(csv.DictReader(f))


class TestCableProfiles(unittest.TestCase):
    def test_the_four_cable_types_are_defined(self):
        for name in ("LMR-240", "LMR-400", "RG-8X", "RG-58"):
            self.assertIn(name, t.CABLE_PROFILES)

    def test_every_profile_has_usable_numbers(self):
        for name in REAL_MATERIALS:
            profile = t.CABLE_PROFILES[name]
            with self.subTest(material=name):
                self.assertEqual(set(profile), {"loss_per_100ft_db", "connector_allowance_db", "max_vswr"})
                self.assertTrue(all(math.isfinite(v) for v in profile.values()))
                self.assertGreater(profile["loss_per_100ft_db"], 0)
                self.assertGreaterEqual(profile["connector_allowance_db"], 0)
                self.assertGreater(profile["max_vswr"], 1.0)

    def test_loss_per_100ft_ranks_with_cable_size(self):
        loss = {n: t.CABLE_PROFILES[n]["loss_per_100ft_db"] for n in ("LMR-400", "LMR-240", "RG-8X", "RG-58")}
        self.assertLess(loss["LMR-400"], loss["LMR-240"])
        self.assertLess(loss["LMR-240"], loss["RG-8X"])
        self.assertLess(loss["RG-8X"], loss["RG-58"])

    def test_default_assembly_keeps_the_old_traveler_limits(self):
        self.assertIn(t.DEFAULT_MATERIAL, t.CABLE_PROFILES)
        self.assertAlmostEqual(t.calc_max_loss(t.DEFAULT_MATERIAL, t.DEFAULT_LENGTH_FT), 0.58, places=9)
        self.assertAlmostEqual(t.CABLE_PROFILES[t.DEFAULT_MATERIAL]["max_vswr"], 1.60, places=9)


class TestMaxLossFormula(TesterCase):
    def test_worked_examples(self):
        # TEST-CABLE: 10 dB per 100 ft plus 0.25 dB for the connectors
        for length, expected in ((0.5, 0.30), (1, 0.35), (20, 2.25), (100, 10.25)):
            with self.subTest(length_ft=length):
                self.assertAlmostEqual(t.calc_max_loss("TEST-CABLE", length), expected, places=9)

    def test_real_profiles_follow_the_formula(self):
        for name in REAL_MATERIALS:
            profile = t.CABLE_PROFILES[name]
            for length in (1, 3.5, 6, 10, 25, 50, 100):
                expected = profile["loss_per_100ft_db"] / 100 * length + profile["connector_allowance_db"]
                with self.subTest(material=name, length_ft=length):
                    self.assertAlmostEqual(t.calc_max_loss(name, length), expected, places=9)

    def test_allowance_is_added_once_whatever_the_length(self):
        for name in REAL_MATERIALS:
            profile = t.CABLE_PROFILES[name]
            slope = (t.calc_max_loss(name, 30) - t.calc_max_loss(name, 10)) / 20
            intercept = t.calc_max_loss(name, 10) - slope * 10
            with self.subTest(material=name):
                self.assertAlmostEqual(slope, profile["loss_per_100ft_db"] / 100, places=9)
                self.assertAlmostEqual(intercept, profile["connector_allowance_db"], places=9)

    def test_cable_type_spelling_does_not_matter(self):
        for spelling in ("lmr-240", "LMR240", "Lmr 240"):
            with self.subTest(spelling=spelling):
                self.assertEqual(t.calc_max_loss(spelling, 10), t.calc_max_loss("LMR-240", 10))

    def test_length_must_be_a_positive_number(self):
        for bad in (0, -1, -0.5, float("nan"), float("inf"), "abc", "", None):
            with self.subTest(length=bad), self.assertRaises(ValueError):
                t.calc_max_loss("LMR-240", bad)

    def test_unknown_cable_type_is_refused(self):
        with self.assertRaises(ValueError):
            t.calc_max_loss("RG-59", 10)

    def test_resolve_limits_prefers_explicit_values(self):
        self.assertEqual(t.resolve_limits("TEST-CABLE", 20), (1.40, t.calc_max_loss("TEST-CABLE", 20)))
        self.assertEqual(t.resolve_limits("TEST-CABLE", 20, max_vswr=1.2), (1.2, 2.25))
        self.assertEqual(t.resolve_limits("TEST-CABLE", 20, max_loss=0.9), (1.40, 0.9))
        self.assertEqual(t.resolve_limits("TEST-CABLE", 20, 1.2, 0.9), (1.2, 0.9))


class TestInputParsing(unittest.TestCase):
    def test_cable_names_in_common_spellings(self):
        for text, key in (("LMR-240", "LMR-240"), ("lmr240", "LMR-240"), (" Lmr 400 ", "LMR-400"),
                          ("rg-8x", "RG-8X"), ("RG8X", "RG-8X"), ("rg58", "RG-58"), ("RG-58", "RG-58")):
            with self.subTest(text=text):
                self.assertEqual(t.normalize_material(text), key)

    def test_unknown_cable_names_are_refused(self):
        for text in ("", "   ", "RG-59", "LMR", "banana", "240"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                t.normalize_material(text)

    def test_lengths(self):
        for value, expected in (("5", 5.0), ("12.5", 12.5), (" 3 ", 3.0), (7, 7.0), (0.5, 0.5)):
            with self.subTest(value=value):
                self.assertEqual(t.parse_length(value), expected)

    def test_bad_lengths_are_refused(self):
        for value in ("", "abc", "0", "-3", "nan", "inf", "-inf", "5 ft", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                t.parse_length(value)


class TestCommandLine(TesterCase):
    def test_defaults_keep_the_old_behaviour(self):
        args = t.parse_args([])
        self.assertEqual(args.material, "LMR-240")
        self.assertEqual(args.length, t.DEFAULT_LENGTH_FT)
        self.assertIsNone(args.max_vswr)
        self.assertIsNone(args.max_loss)
        setup = t.Setup(args.material, args.length, args.max_vswr, args.max_loss)
        self.assertAlmostEqual(setup.max_loss, 0.58, places=9)
        self.assertAlmostEqual(setup.max_vswr, 1.60, places=9)

    def test_material_and_length_flags(self):
        args = t.parse_args(["--material", "rg-58", "--length", "12.5"])
        self.assertEqual(args.material, "RG-58")
        self.assertEqual(args.length, 12.5)

    def test_flags_reject_bad_values_with_a_usage_error(self):
        for argv in (["--material", "RG-59"], ["--length", "0"], ["--length", "-4"],
                     ["--length", "abc"], ["--length", "nan"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    t.parse_args(argv)
                self.assertEqual(caught.exception.code, 2)

    def test_explicit_limit_flags_still_override(self):
        args = t.parse_args(["--material", "TEST-CABLE", "--length", "20", "--max-vswr", "1.35", "--max-loss", "0.45"])
        setup = t.Setup(args.material, args.length, args.max_vswr, args.max_loss)
        self.assertEqual((setup.max_vswr, setup.max_loss), (1.35, 0.45))


class TestSetup(TesterCase):
    def test_limits_follow_cable_and_length(self):
        setup = t.Setup("TEST-CABLE", 20)
        self.assertAlmostEqual(setup.max_loss, 2.25, places=9)
        self.assertEqual(setup.max_vswr, 1.40)
        setup.set_length(30)
        self.assertAlmostEqual(setup.max_loss, 3.25, places=9)
        setup.set_material("TEST-TIGHT")
        self.assertEqual(setup.max_vswr, 1.30)

    def test_changing_length_drops_only_the_loss_override(self):
        setup = t.Setup("TEST-CABLE", 10, max_vswr=1.25, max_loss=0.9)
        self.assertEqual(setup.set_length(20), ["--max-loss"])
        self.assertAlmostEqual(setup.max_loss, 2.25, places=9)
        self.assertEqual(setup.max_vswr, 1.25)

    def test_changing_material_drops_both_overrides(self):
        setup = t.Setup("TEST-CABLE", 10, max_vswr=1.25, max_loss=0.9)
        self.assertEqual(setup.set_material("TEST-TIGHT"), ["--max-vswr", "--max-loss"])
        self.assertEqual(setup.max_vswr, 1.30)
        self.assertAlmostEqual(setup.max_loss, t.calc_max_loss("TEST-TIGHT", 10), places=9)

    def test_choosing_what_is_already_set_keeps_the_overrides(self):
        setup = t.Setup("TEST-CABLE", 10, max_vswr=1.25, max_loss=0.9)
        self.assertEqual(setup.set_material("test cable"), [])
        self.assertEqual(setup.set_length("10"), [])
        self.assertEqual((setup.max_vswr, setup.max_loss), (1.25, 0.9))

    def test_a_refused_change_leaves_the_setup_alone(self):
        setup = t.Setup("TEST-CABLE", 10, max_loss=0.9)
        for call, bad in ((setup.set_material, "RG-59"), (setup.set_length, "x"), (setup.set_length, -2)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                call(bad)
        self.assertEqual((setup.material, setup.length_ft, setup.max_loss), ("TEST-CABLE", 10.0, 0.9))

    def test_describe_states_cable_length_and_limits(self):
        self.assertEqual(t.Setup("TEST-CABLE", 20).describe(),
                         "TEST-CABLE, 20 ft: VSWR <= 1.40:1, Loss <= 2.25 dB")


class TestSimulatedMeasurement(TesterCase):
    def test_reads_the_1ghz_point_and_not_a_neighbour(self):
        fake = FakeNanoVNA(vswr=1.37, loss_db=0.52)
        vswr, loss_db, detail = t.measure(fake, settle=0)
        self.assertAlmostEqual(vswr, 1.37, places=3)
        self.assertAlmostEqual(loss_db, 0.52, places=3)
        self.assertEqual(detail, "")
        self.assertIn("sweep 900000000 1100000000 101", fake.commands)

    def test_unconfirmed_calibration_is_still_refused(self):
        with self.assertRaises(t.InvalidReading):
            t.measure(FakeNanoVNA(cal_reply="load open short"), settle=0)


class TestLimitsByCable(TesterCase):
    def test_loss_just_inside_and_just_outside_the_limit_for_every_cable(self):
        for name in REAL_MATERIALS:
            for length in (3, 10, 50):
                limit = t.calc_max_loss(name, length)
                with self.subTest(material=name, length_ft=length):
                    self.assertEqual(self.scan(FakeNanoVNA(vswr=1.2, loss_db=limit - 0.03), name, length), "PASS")
                    self.assertEqual(self.scan(FakeNanoVNA(vswr=1.2, loss_db=limit + 0.03), name, length), "FAIL")

    def test_the_same_reading_passes_on_a_longer_assembly_and_fails_on_a_shorter_one(self):
        reading = (t.calc_max_loss("LMR-400", 10) + t.calc_max_loss("LMR-400", 25)) / 2
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=reading), "LMR-400", 10), "FAIL")
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=reading), "LMR-400", 25), "PASS")

    def test_the_same_reading_and_length_differ_by_cable_type(self):
        reading = (t.calc_max_loss("LMR-400", 10) + t.calc_max_loss("RG-58", 10)) / 2
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=reading), "LMR-400", 10), "FAIL")
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=reading), "RG-58", 10), "PASS")

    def test_vswr_limit_comes_from_the_profile(self):
        # Same cable data and same reading; only the profile's VSWR limit differs
        self.assertEqual(self.scan(FakeNanoVNA(vswr=1.50, loss_db=0.5), "TEST-TIGHT", 10), "FAIL")
        self.assertEqual(self.scan(FakeNanoVNA(vswr=1.50, loss_db=0.5), "TEST-LOOSE", 10), "PASS")

    def test_vswr_just_inside_and_just_outside_each_real_profile(self):
        for name in REAL_MATERIALS:
            limit = t.CABLE_PROFILES[name]["max_vswr"]
            with self.subTest(material=name):
                self.assertEqual(self.scan(FakeNanoVNA(vswr=limit - 0.05, loss_db=0.1), name, 10), "PASS")
                self.assertEqual(self.scan(FakeNanoVNA(vswr=limit + 0.05, loss_db=0.1), name, 10), "FAIL")

    def test_explicit_limits_override_the_profile(self):
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=0.40), "LMR-240", 5), "PASS")
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=0.40), "LMR-240", 5, max_loss=0.30), "FAIL")
        self.assertEqual(self.scan(FakeNanoVNA(vswr=1.50), "LMR-240", 5), "PASS")
        self.assertEqual(self.scan(FakeNanoVNA(vswr=1.50), "LMR-240", 5, max_vswr=1.40), "FAIL")

    def test_result_block_names_the_cable_and_its_limits(self):
        with quiet() as out:
            t.ensure_log(self.log)
            t.run_test(FakeNanoVNA(loss_db=0.5), "SN-1", self.log, settle=0, material="TEST-CABLE", length_ft=20)
        text = out.getvalue()
        self.assertIn("Cable         : TEST-CABLE, 20 ft", text)
        self.assertIn("(Limit: <= 2.25 dB)", text)
        self.assertIn("(Limit: <= 1.40:1)", text)


class TestLogRows(TesterCase):
    def test_header_has_the_new_columns_at_the_end(self):
        t.ensure_log(self.log)
        with open(self.log, newline="") as f:
            header = next(csv.reader(f))
        self.assertEqual(header, t.CSV_HEADER)
        self.assertEqual(header[-2:], ["Material_ID", "Length_Ft"])

    def test_earlier_columns_keep_their_positions(self):
        self.assertEqual(t.CSV_HEADER[:8], LEGACY_HEADER)

    def test_pass_row_records_cable_length_and_limits(self):
        self.assertEqual(self.scan(FakeNanoVNA(vswr=1.25, loss_db=0.80), "RG-58", 4.5, sn="RG58-0001"), "PASS")
        (row,) = self.rows()
        self.assertEqual(row["Serial_Number"], "RG58-0001")
        self.assertEqual(row["Status"], "PASS")
        self.assertEqual(row["Material_ID"], "RG-58")
        self.assertEqual(row["Length_Ft"], "4.50")
        self.assertEqual(row["Limit_Loss_dB"], f"{t.calc_max_loss('RG-58', 4.5):.2f}")
        self.assertEqual(row["Limit_VSWR"], f"{t.CABLE_PROFILES['RG-58']['max_vswr']:.2f}")
        self.assertEqual(row["VSWR_1GHz"], "1.25")
        self.assertEqual(row["Loss_dB_1GHz"], "0.80")

    def test_invalid_and_error_rows_carry_the_cable_too(self):
        self.assertEqual(self.scan(FakeNanoVNA(cal_reply="load open short"), "RG-8X", 7.5, sn="A"), "INVALID")
        self.assertEqual(self.scan(FakeNanoVNA(loss_db=-1.0), "RG-8X", 7.5, sn="B"), "INVALID")
        self.assertEqual(self.scan(FakeNanoVNA(points=100), "RG-8X", 7.5, sn="C"), "ERROR")
        rows = self.rows()
        self.assertEqual([r["Status"] for r in rows], ["INVALID", "INVALID", "ERROR"])
        for row in rows:
            with self.subTest(serial=row["Serial_Number"]):
                self.assertEqual((row["Material_ID"], row["Length_Ft"]), ("RG-8X", "7.50"))
                self.assertEqual(row["Limit_Loss_dB"], f"{t.calc_max_loss('RG-8X', 7.5):.2f}")

    def test_a_caller_without_cable_details_leaves_the_new_fields_empty(self):
        t.ensure_log(self.log)
        with quiet():
            t.log_result(self.log, "SN-1", 1.2, 0.3, "PASS", "", 1.6, 0.58)
        (row,) = self.rows()
        self.assertEqual((row["Material_ID"], row["Length_Ft"]), ("", ""))


class TestLogUpgrade(TesterCase):
    def write_log(self, header, *rows):
        with open(self.log, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    def ensure(self):
        with quiet() as out:
            t.ensure_log(self.log)
        return out.getvalue()

    def read_all(self):
        with open(self.log, newline="") as f:
            return list(csv.reader(f))

    def backups(self):
        return sorted(self.dir.glob("results.pre-upgrade-*.csv"))

    def test_a_missing_or_empty_log_gets_the_full_header(self):
        self.ensure()
        self.assertEqual(self.read_all(), [t.CSV_HEADER])
        self.log.write_text("")
        self.ensure()
        self.assertEqual(self.read_all(), [t.CSV_HEADER])

    def test_an_eight_column_log_is_upgraded_and_a_copy_kept(self):
        old = ["2026-10-01 09:00:00", "SN-9", "1.30", "0.41", "PASS", "1.60", "0.58", 'note, with "quotes"']
        self.write_log(LEGACY_HEADER, old)
        original = self.log.read_bytes()
        message = self.ensure()
        rows = self.read_all()
        self.assertEqual(rows[0], t.CSV_HEADER)
        self.assertEqual(rows[1], old + ["", ""])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), original)
        self.assertIn("Material_ID", message)
        # New rows append cleanly under the upgraded header
        with quiet():
            t.log_result(self.log, "SN-10", 1.2, 0.3, "PASS", "", 1.6, 0.58, "LMR-240", 5.0)
        old_row, new_row = self.rows()
        self.assertEqual((old_row["Material_ID"], old_row["Length_Ft"]), ("", ""))
        self.assertEqual((new_row["Serial_Number"], new_row["Material_ID"], new_row["Length_Ft"]),
                         ("SN-10", "LMR-240", "5.00"))

    def test_the_original_five_column_log_with_mixed_row_widths(self):
        five = ["2026-09-01 08:00:00", "SN-1", "1.20", "0.30", "PASS"]
        eight = ["2026-09-02 08:00:00", "SN-2", "1.30", "0.45", "FAIL", "1.60", "0.58", "re-test"]
        self.write_log(LEGACY_HEADER[:5], five, eight)
        self.ensure()
        rows = self.read_all()
        self.assertEqual(rows[0], t.CSV_HEADER)
        self.assertEqual(rows[1], five + [""] * 5)
        self.assertEqual(rows[2], eight + ["", ""])
        self.assertEqual(len(self.backups()), 1)

    def test_a_current_log_is_left_alone(self):
        self.ensure()
        with quiet():
            t.log_result(self.log, "SN-1", 1.2, 0.3, "PASS", "", 1.6, 0.58, "LMR-240", 5.0)
        before = self.log.read_bytes()
        self.assertEqual(self.ensure(), "")
        self.assertEqual(self.log.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_an_unrecognised_log_is_refused_and_untouched(self):
        self.log.write_text("a,b,c\n1,2,3\n")
        before = self.log.read_bytes()
        with self.assertRaises(OSError):
            self.ensure()
        self.assertEqual(self.log.read_bytes(), before)
        self.assertEqual(self.backups(), [])
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class TestBatchSession(TesterCase):
    def session(self, answers, fake=None, **setup_args):
        """Runs the serial-number prompt with scripted answers. Returns (setup, input script, output)."""
        setup = t.Setup(**setup_args)
        script = ScriptedInput(answers)
        with mock.patch("builtins.input", script), quiet() as out:
            t.ensure_log(self.log)
            t.batch_loop(fake or FakeNanoVNA(vswr=1.20, loss_db=0.30), setup, self.log, settle=0)
        return setup, script, out.getvalue()

    def test_m_and_l_change_what_the_following_cables_are_tested_against(self):
        answers = ["SN-1",
                   "m", "2", "SN-2",         # menu number 2: the second profile
                   "L", "10", "SN-3",        # upper case works too; changes the length only
                   "M", "rg-58", "SN-4",     # by name; the 10 ft stays
                   "q"]
        setup, _, _ = self.session(answers)
        expected = [("LMR-240", 5), (REAL_MATERIALS[1], 5), (REAL_MATERIALS[1], 10), ("RG-58", 10)]
        rows = self.rows()
        self.assertEqual([(r["Material_ID"], r["Length_Ft"]) for r in rows],
                         [(m, f"{length:.2f}") for m, length in expected])
        self.assertEqual([r["Limit_Loss_dB"] for r in rows],
                         [f"{t.calc_max_loss(m, length):.2f}" for m, length in expected])
        self.assertEqual([r["Serial_Number"] for r in rows], ["SN-1", "SN-2", "SN-3", "SN-4"])
        self.assertEqual((setup.material, setup.length_ft), ("RG-58", 10.0))

    def test_bad_or_blank_answers_keep_the_current_cable(self):
        answers = ["m", "banana", "SN-1", "m", "9", "SN-2", "m", "", "SN-3",
                   "l", "abc", "SN-4", "l", "-5", "SN-5", "l", "0", "SN-6", "l", "nan", "SN-7",
                   "l", "", "SN-8", "q"]
        setup, _, out = self.session(answers)
        rows = self.rows()
        self.assertEqual(len(rows), 8)
        self.assertEqual({(r["Material_ID"], r["Length_Ft"]) for r in rows}, {("LMR-240", "5.00")})
        self.assertEqual((setup.material, setup.length_ft), ("LMR-240", 5.0))
        self.assertIn("keeping", out)

    def test_the_prompt_shows_the_active_cable(self):
        _, script, _ = self.session(["m", "lmr400", "q"])
        scan_prompts = [p for p in script.prompts if "Serial Number" in p]
        self.assertIn("[LMR-240, 5 ft]", scan_prompts[0])
        self.assertIn("[LMR-400, 5 ft]", scan_prompts[1])

    def test_overrides_are_dropped_when_the_assembly_they_were_set_for_changes(self):
        answers = ["SN-1", "l", "6", "SN-2", "m", "rg58", "SN-3", "q"]
        _, _, out = self.session(answers, material="LMR-240", length_ft=5, max_vswr=1.35, max_loss=0.45)
        rows = self.rows()
        self.assertEqual([r["Limit_VSWR"] for r in rows],
                         ["1.35", "1.35", f"{t.CABLE_PROFILES['RG-58']['max_vswr']:.2f}"])
        self.assertEqual([r["Limit_Loss_dB"] for r in rows],
                         ["0.45", f"{t.calc_max_loss('LMR-240', 6):.2f}", f"{t.calc_max_loss('RG-58', 6):.2f}"])
        self.assertIn("--max-loss cleared", out)     # the length change dropped the loss override
        self.assertIn("--max-vswr cleared", out)     # the material change dropped what was left

    def test_serial_numbers_that_only_start_with_m_or_l_are_cables(self):
        self.session(["m1", "L-7", "lm", "q"])
        self.assertEqual([r["Serial_Number"] for r in self.rows()], ["m1", "L-7", "lm"])

    def test_blank_lines_are_skipped_and_exit_quits(self):
        self.session(["", "   ", "SN-1", "exit"])
        self.assertEqual([r["Serial_Number"] for r in self.rows()], ["SN-1"])


class TestMain(TesterCase):
    def run_main(self, argv, answers, fake):
        """Runs main() against the simulated NanoVNA. Returns (input script, output)."""
        script = ScriptedInput(answers)
        full_argv = list(argv) + ["--port", "FAKE", "--log", str(self.log), "--settle", "0"]
        with mock.patch.object(t.serial, "Serial", return_value=fake), \
                mock.patch("builtins.input", script), quiet() as out:
            t.main(full_argv)
        return script, out.getvalue()

    def test_flags_pick_the_cable_and_the_banner_shows_the_limits(self):
        fake = FakeNanoVNA(vswr=1.2, loss_db=0.9)
        _, out = self.run_main(["--material", "RG-58", "--length", "10"], ["SN-1", "q"], fake)
        limit = t.calc_max_loss("RG-58", 10)
        self.assertIn("Cable : RG-58, 10 ft", out)
        self.assertIn(f"Loss <= {limit:.2f} dB", out)
        (row,) = self.rows()
        self.assertEqual((row["Material_ID"], row["Length_Ft"], row["Status"]), ("RG-58", "10.00", "PASS"))
        self.assertEqual(row["Limit_Loss_dB"], f"{limit:.2f}")
        self.assertTrue(fake.closed)

    def test_runtime_changes_start_from_what_the_flags_set(self):
        fake = FakeNanoVNA(vswr=1.2, loss_db=0.3)
        self.run_main(["--material", "LMR-400", "--length", "10"], ["SN-1", "l", "20", "SN-2", "q"], fake)
        self.assertEqual([(r["Material_ID"], r["Length_Ft"]) for r in self.rows()],
                         [("LMR-400", "10.00"), ("LMR-400", "20.00")])

    def test_explicit_limit_flags_are_shown_and_logged(self):
        _, out = self.run_main(["--max-loss", "0.45", "--max-vswr", "1.35"], ["SN-1", "q"], FakeNanoVNA())
        self.assertIn("VSWR <= 1.35:1, Loss <= 0.45 dB", out)
        (row,) = self.rows()
        self.assertEqual((row["Limit_VSWR"], row["Limit_Loss_dB"]), ("1.35", "0.45"))

    def test_startup_upgrades_an_older_log(self):
        old = ["2026-10-01 09:00:00", "SN-9", "1.30", "0.41", "PASS", "1.60", "0.58", ""]
        with open(self.log, "w", newline="") as f:
            csv.writer(f).writerows([LEGACY_HEADER, old])
        self.run_main([], ["SN-1", "q"], FakeNanoVNA())
        with open(self.log, newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], t.CSV_HEADER)
        self.assertEqual(rows[1], old + ["", ""])
        self.assertEqual(rows[2][-2:], ["LMR-240", "5.00"])

    def test_bad_flag_values_exit_with_a_usage_error(self):
        for argv in (["--material", "nonsense"], ["--length", "-1"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    t.main(argv)
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
