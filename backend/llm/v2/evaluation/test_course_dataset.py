"""Offline data integrity only. No provider, course generation, DB or quality judge."""
import ast
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import unittest
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
DATA = Path(__file__).with_name("course_dataset_20261013")


def read(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class CourseDatasetIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.data = read("cases.json")
        self.fixtures = read("fixtures.json")["fixtures"]
        self.cases = self.data["cases"]

    def test_ids_scope_and_no_fabricated_execution(self):
        self.assertEqual(self.data["status"], "draft-needs-independent-review")
        self.assertEqual(len(self.cases), 20)
        self.assertEqual(len({c["id"] for c in self.cases}), 20)
        self.assertEqual(sum(c["tier"] == "core" for c in self.cases), 6)
        self.assertEqual(sum(c["method"] == "browser" for c in self.cases), 2)
        self.assertEqual(self.data["measurement_plan"]["live_executions_performed"], 0)
        self.assertEqual(self.data["measurement_plan"]["planned_core_case_executions"], 6 * 3 * 2)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertRegex(case["id"], r"^(C0[1-6]|R(0[1-9]|1[0-4]))$")
                self.assertEqual(case["review_status"], "draft-needs-independent-review")
                self.assertTrue(case["turns"])
                self.assertTrue(all(t["input"].strip() and t["role"] == "user" for t in case["turns"]))

    def test_expected_visits_references_and_source_policies(self):
        phases = {"BEFORE": 0, "GAME": 1, "AFTER": 2}
        kinds = {"FOOD", "CAFE", "BAR", "WALK", "STAY", "STADIUM"}
        for case in self.cases:
            with self.subTest(case=case["id"]):
                checks = case["expected"]["checks"]
                self.assertEqual(len(checks), len(set(checks)))
                self.assertTrue(set(checks) <= set(self.data["rubric"]))
                base = case["setup"]["base_fixture"]
                self.assertTrue(base is None or base in self.fixtures)
                visits = case["expected"]["visits"]
                self.assertTrue(visits)
                self.assertTrue(all(v["kind"] in kinds for v in visits))
                self.assertEqual(sum(v["kind"] == "STADIUM" for v in visits), 1)
                self.assertEqual([phases[v["phase"]] for v in visits], sorted(phases[v["phase"]] for v in visits))
                for visit in visits:
                    if visit.get("menu"):
                        self.assertIn("menu", checks)
                    if visit.get("brand"):
                        self.assertIn("brand", checks)
                    if visit.get("internal"):
                        self.assertEqual(visit["source"], "myseatcheck")
                        self.assertIn("internal", checks)
                    if visit["kind"] == "BAR":
                        self.assertTrue(visit["rating_required"])
                        self.assertIn("bar_rating", checks)
                    if visit["kind"] == "STAY":
                        self.assertEqual(visit["source"], "nol")
                        self.assertIn("hotel", checks)

    def test_fixed_inputs_arrival_times_and_edit_targets(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                expected, setup = case["expected"], case["setup"]
                date.fromisoformat(expected["game"]["date"])
                self.assertIs(expected["game"]["isDemo"], True)
                kickoff = datetime.strptime(expected["game"]["time"], "%H:%M")
                for field, offset in (("internal_arrival", 30), ("stadium_arrival", 20)):
                    if field in expected:
                        self.assertEqual(expected[field], (kickoff - timedelta(minutes=offset)).strftime("%H:%M"))
                if setup.get("no_origin_anywhere"):
                    self.assertNotIn("origin", setup["context"])
                    self.assertIsNone(setup["base_fixture"])
                    self.assertIn("default_origin", expected)
                base = setup["base_fixture"]
                if "preserve" in expected["checks"]:
                    self.assertIsNotNone(base)
                    original = self.fixtures[base]["current_course"]
                    self.assertEqual(original["game"], expected["game"])
                    count = len(original["places"])
                    indices = expected["changed_visit_indices"]
                    self.assertEqual(len(indices), len(set(indices)))
                    self.assertTrue(all(type(i) is int and 0 <= i < count for i in indices))
                    self.assertTrue(set(expected.get("deleted_visit_indices", [])) <= set(indices))
                    if "duration_override" in expected:
                        self.assertTrue(expected["preserve_exceptions"])
                if case["method"] == "browser":
                    self.assertIn("ui", expected["checks"])

    def test_fixture_hashes_sanitized_keys_and_real_place_fields(self):
        allowed = {"stadiumCode", "game", "origin", "places", "selectedPlace", "travelMode", "legModes"}
        forbidden = {"session", "session_id", "user_id", "token", "password", "api_key", "writerState", "coursePayload"}
        def inspect_keys(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden)
                for child in value.values():
                    inspect_keys(child)
            elif isinstance(value, list):
                for child in value:
                    inspect_keys(child)
        for key, fixture in self.fixtures.items():
            with self.subTest(fixture=key):
                course = fixture["current_course"]
                inspect_keys(fixture)
                self.assertTrue(set(course) <= allowed)
                canonical = json.dumps(course, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                self.assertEqual(hashlib.sha256(canonical).hexdigest(), fixture["current_course_sha256"])
                self.assertRegex(fixture["source_sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue((ROOT / fixture["source_report"]).is_file())
                for place in course["places"]:
                    self.assertTrue(place["name"] and place["visitId"])
                    self.assertTrue(math.isfinite(place["lat"]) and -90 <= place["lat"] <= 90)
                    self.assertTrue(math.isfinite(place["lng"]) and -180 <= place["lng"] <= 180)
                    if place["category"] != "STADIUM":
                        self.assertIn(urlparse(place["placeUrl"]).hostname, {"place.map.kakao.com", "myseatcheck.com"})
        selected = self.fixtures["jamsil_selected_cafe"]["current_course"]
        self.assertEqual(selected["places"], [selected["selectedPlace"]])
        self.assertNotIn("game", selected)

    def test_template_unknown_values_are_not_zero_or_pass(self):
        template = read("results.template.json")
        self.assertEqual(template["dataset_id"], self.data["dataset_id"])
        self.assertIsNone(template["run_id"])
        self.assertIsNone(template["dataset_sha256"])
        self.assertEqual([r["case_id"] for r in template["results"]], [c["id"] for c in self.cases])
        for row, case in zip(template["results"], self.cases):
            with self.subTest(case=case["id"]):
                self.assertEqual(row["status"], "not_run")
                self.assertIsNone(row["task_complete"])
                for field in ("done_seconds", "stream_end_seconds", "map_applied_seconds", "input_tokens", "output_tokens", "cost_usd"):
                    self.assertIsNone(row[field])
                self.assertTrue(all(value is None for value in row["calls"].values()))
                self.assertEqual(set(row["checks"]), set(case["expected"]["checks"]))
                self.assertTrue(all(check["status"] == "not_run" and not check["evidence_refs"] for check in row["checks"].values()))

    def test_free_ci_includes_this_exact_file(self):
        tree = ast.parse((ROOT / ".github/evaluation_ci.py").read_text(encoding="utf-8"))
        free = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FREE" for t in n.targets))
        self.assertTrue(any(isinstance(n, ast.Constant) and n.value == "test_course_dataset.py" for n in ast.walk(free)))


if __name__ == "__main__":
    unittest.main()
