"""Synthetic counterexamples for record consistency; no actual RSI observations."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name("rsi_records.py")
spec = importlib.util.spec_from_file_location("rsi_records", MODULE)
rsi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rsi)
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def fixture():
    effort = {"active_seconds": 60, "cost_amount": 1, "currency": "USD", "evidence": "synthetic://effort"}
    tasks, weeks = [], []
    for i in range(4):
        binding = {"repo": sorted(rsi.REPOSITORIES)[i % 2], "snapshot": "fixture-snapshot",
                   "profile": "fixture-profile", "objective": "review correctness",
                   "denominator": "one matched task", "unit": "seconds", "workload": "fixture-task"}
        tasks.append({"id": "t" + str(i), "manual_binding": binding,
                      "assisted_binding": dict(binding), "manual": dict(effort),
                      "assisted": dict(effort, active_seconds=45),
                      "checks": [{"id": "mandatory", "outcome": "pass", "evidence": "synthetic://check"}],
                      "exposure": "synthetic exposed fixture; no independence claim", "evidence": "synthetic://task"})
        start = NOW - timedelta(days=35 - 7 * i)
        end = start + timedelta(days=7)
        weeks.append({"id": "w" + str(i), "start": stamp(start), "end": stamp(end),
                      "recorded_at": stamp(end), "task_ids": ["t" + str(i)], "evidence": "synthetic://week"})
    return {"schema": "dimaggi-rsi-records/v1", "evidence_class": "synthetic",
            "repositories": sorted(rsi.REPOSITORIES), "required_checks": ["mandatory"],
            "tasks": tasks, "weeks": weeks, "setup": dict(effort, active_seconds=120),
            "dispositions": [{"id": "d" + str(i), "finding_id": "f" + str(i), "task_id": "t" + str(i),
                              "kind": "justified_no_change", "prior_known": False,
                              "owner": "fixture-owner", "accepted_at": stamp(NOW - timedelta(days=1)),
                              "evidence": "synthetic://decision"} for i in range(2)]}


class RecordTests(unittest.TestCase):
    def test_synthetic_records_never_authorize_continuation(self):
        result = rsi.assess(fixture(), NOW)
        self.assertEqual(result["assessment"], "synthetic_only")
        self.assertEqual(result["recorded_gate"], "recorded_criteria_met")
        self.assertFalse(result["continuation_authorized"])
        self.assertFalse(result["candidate_execution_authorized"])
        self.assertEqual(result["effort_and_cost"]["assisted_seconds_including_setup"], 300)

    def test_observed_label_is_not_authentication(self):
        record = fixture()
        record["evidence_class"] = "observed"  # Deliberate untrusted-label test.
        result = rsi.assess(record, NOW)
        self.assertEqual(result["assessment"], "recorded_criteria_met")
        self.assertFalse(result["human_reports_verified"])
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["continuation_authorized"])

    def test_empty_record_is_insufficient(self):
        record = fixture()
        record.update(tasks=[], weeks=[], dispositions=[])
        self.assertEqual(rsi.assess(record, NOW)["recorded_gate"], "insufficient_evidence")

    def test_changed_denominator_or_snapshot_is_incomparable(self):
        for field in ("denominator", "snapshot", "objective", "profile", "unit", "workload"):
            with self.subTest(field=field):
                record = fixture()
                record["tasks"][0]["assisted_binding"][field] = "changed"
                result = rsi.assess(record, NOW)
                self.assertEqual(result["recorded_gate"], "not_met")
                self.assertTrue(any("incomparable" in x for x in result["gate_failures"]))

    def test_duplicate_findings_and_task_credit_refused(self):
        record = fixture()
        record["dispositions"][1]["finding_id"] = "f0"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            rsi.assess(record, NOW)
        record = fixture()
        record["weeks"][1]["task_ids"] = ["t0"]
        with self.assertRaisesRegex(ValueError, "multiple weeks"):
            rsi.assess(record, NOW)

    def test_prior_known_findings_do_not_earn_usefulness_credit(self):
        record = fixture()
        record["dispositions"][0]["prior_known"] = True
        result = rsi.assess(record, NOW)
        self.assertEqual(result["useful_dispositions"], 1)
        self.assertEqual(result["recorded_gate"], "not_met")

    def test_missing_mandatory_checks_and_measurements_stay_unknown(self):
        for mutation in (lambda r: r["tasks"][0].update(checks=[]),
                         lambda r: r["tasks"][0]["manual"].update(active_seconds=None),
                         lambda r: r["tasks"][0]["assisted"].update(cost_amount=None),
                         lambda r: r["setup"].update(cost_amount=None),
                         lambda r: r["dispositions"][0].update(owner=None)):
            record = fixture()
            mutation(record)
            result = rsi.assess(record, NOW)
            self.assertEqual(result["recorded_gate"], "insufficient_evidence")
            self.assertTrue(result["missing"])
            self.assertTrue(all(value is None for value in result["effort_and_cost"].values()))

    def test_failed_check_and_effort_increase_fail_gate(self):
        record = fixture()
        record["tasks"][0]["checks"][0]["outcome"] = "fail"
        self.assertEqual(rsi.assess(record, NOW)["recorded_gate"], "not_met")
        record = fixture()
        record["tasks"][0]["assisted"]["active_seconds"] = 1000
        self.assertEqual(rsi.assess(record, NOW)["recorded_gate"], "not_met")

    def test_nonfinite_boolean_negative_and_huge_numbers_refused(self):
        for value in (True, False, -1, float("nan"), float("inf"), "1", 10 ** 1000):
            record = fixture()
            record["tasks"][0]["manual"]["active_seconds"] = value
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                rsi.assess(record, NOW)
        record = fixture()
        for task in record["tasks"]:
            task["manual"]["active_seconds"] = 1e308
        with self.assertRaisesRegex(ValueError, "aggregate"):
            rsi.assess(record, NOW)

    def test_strict_json_duplicates_nonfinite_size_and_unicode(self):
        for raw in (b'{"x":1,"x":2}', b'{"schema":1,"sc\\u0068ema":2}', b'{"x":NaN}',
                    b'{"x":1e999}', b'{"x":"\\ud800"}', b'\xff', b' ' * (rsi.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                rsi.load(raw)

    def test_scope_extensions_and_currency_changes_refused(self):
        record = fixture()
        record["repositories"].append("third-repo")
        with self.assertRaisesRegex(ValueError, "exactly"):
            rsi.assess(record, NOW)
        record = fixture()
        record["candidate_execution_authorized"] = True
        with self.assertRaisesRegex(ValueError, "exact fields"):
            rsi.assess(record, NOW)
        record = fixture()
        record["tasks"][0]["manual"]["currency"] = "EUR"
        with self.assertRaisesRegex(ValueError, "currencies"):
            rsi.assess(record, NOW)

    def test_same_day_overlapping_future_and_missing_weeks(self):
        for mutation in (lambda r: r["weeks"][0].update(end=r["weeks"][0]["start"]),
                         lambda r: r["weeks"][1].update(start=r["weeks"][0]["start"], end=r["weeks"][0]["end"]),
                         lambda r: r["weeks"][-1].update(recorded_at=stamp(NOW + timedelta(days=1)))):
            record = fixture()
            mutation(record)
            with self.assertRaises(ValueError):
                rsi.assess(record, NOW)
        record = fixture()
        record["weeks"].pop()
        self.assertEqual(rsi.assess(record, NOW)["recorded_gate"], "insufficient_evidence")

    def test_completed_window_may_be_recorded_later_but_not_early(self):
        record = fixture()
        record["weeks"][0]["recorded_at"] = stamp(NOW - timedelta(days=1))
        self.assertEqual(rsi.assess(record, NOW)["recorded_gate"], "recorded_criteria_met")
        record["weeks"][0]["recorded_at"] = record["weeks"][0]["start"]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            rsi.assess(record, NOW)

    def test_cli_optimized_refuses_duplicate_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text('{"schema":1,"schema":2}')
            result = subprocess.run([sys.executable, "-O", str(MODULE), "--input", str(path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("duplicate", result.stderr)


if __name__ == "__main__":
    unittest.main()
