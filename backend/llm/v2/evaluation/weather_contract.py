"""Bounded offline weather tool contracts; not final-answer/provider quality."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
KST = timezone(timedelta(hours=9))
WEATHER = {"label": "맑음", "forecastAt": "2026-10-13T10:00+09:00"}
BASE = {"stadium_code": "JAMSIL", "game_date": "2026-10-13"}
NEXT = "오늘 다음 정시 기준(경기 시각 아님)"
LAST = "오늘 마지막 정시 예보 기준(경기 시각 아님)"


def case(id, fields, now, response, time, basis=None):
    expected = None if response is None else {
        **response, "precipitation": response.get("label") if response.get("label") in
        {"비", "비/눈", "눈", "소나기"} else "없음"}
    if basis is not None:
        expected = {"available": False, "requestedTime": time, "timeBasis": basis,
                    "message": "기상청 단기예보에 해당 시각 예보가 아직 없다"} if response is None else {
            **expected, "requestedTime": time, "timeBasis": basis}
    return {"id": id, "input": {**BASE, **fields}, "now": now, "response": response,
            "expected": {"result": expected, "calls": [["JAMSIL", fields.get("game_date", BASE["game_date"]), time]]}}


CASES = [
    case("omitted-none", {}, "09:40", None, "10:00", NEXT),
    case("null-none", {"game_time": None}, "09:40", None, "10:00", NEXT),
    case("explicit-none", {"game_time": "10:00"}, "09:40", None, "10:00"),
    case("explicit-result", {"game_time": "18:30"}, "09:40", WEATHER, "18:30"),
    case("today-next", {}, "09:40", WEATHER, "10:00", NEXT),
    case("today-mismatch", {}, "08:40", WEATHER, "09:00", NEXT + ", 실제 예보 시각 10:00"),
    case("future-mismatch", {"game_date": "2026-10-14"}, "09:40",
         {**WEATHER, "forecastAt": "2026-10-14T19:00+09:00"}, "18:30",
         "18:30 기준(경기 시각 아님), 실제 예보 시각 19:00"),
    *[case("last-" + now, {}, now, {**WEATHER, "forecastAt": "2026-10-13T23:00+09:00"},
           "23:00", LAST) for now in ("23:00", "23:30", "23:59")],
    {"id": "invalid-time", "input": {**BASE, "game_time": "25:00"}, "now": "09:40", "response": WEATHER,
     "expected": {"result": "도구 입력 형식이 올바르지 않습니다. 인자 설명을 확인하세요.", "calls": []}},
]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def validated_rows(report):
    rows = report.get("results") if isinstance(report, dict) else None
    if (not isinstance(rows, list) or len(rows) != len(CASES)
            or report.get("cases_sha256") != hashlib.sha256(encoded(CASES).encode()).hexdigest()
            or any(not isinstance(r, dict) or "actual" not in r
                   or r.get("id") != c["id"] or "expected" not in r
                   or encoded(r["expected"]) != encoded(c["expected"])
                   for r, c in zip(rows, CASES))):
        raise ValueError("Frozen cases mismatch")
    return rows


def deep_scores(rows):
    from deepeval import evaluate
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase
    from deepeval.evaluate import AsyncConfig, CacheConfig, DisplayConfig

    class WeatherContract(BaseMetric):
        threshold = 1.0
        async_mode = False
        strict_mode = True
        evaluation_model = "deterministic exact tool contract (no judge)"

        @property
        def __name__(self):
            return "weather_contract"

        def measure(self, test_case, *args, **kwargs):
            self.score = float(test_case.actual_output == test_case.expected_output)
            self.success = self.score == 1
            self.reason = "Exact returned value and service arguments; no semantic quality measured"
            return self.score

        async def a_measure(self, test_case, *args, **kwargs):
            return self.measure(test_case)

        def is_successful(self):
            return self.success

    result = evaluate([LLMTestCase(input=r["id"], actual_output=encoded(r["actual"]),
                                  expected_output=encoded(r["expected"])) for r in rows],
                      metrics=[WeatherContract()], async_config=AsyncConfig(run_async=False),
                      cache_config=CacheConfig(write_cache=False, use_cache=False),
                      display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False))
    return [r.metrics_data[0].score for r in result.test_results]


def ragas_scores(rows):
    from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate
    from ragas.metrics.base import MetricType, SingleTurnMetric

    class WeatherContract(SingleTurnMetric):
        def __init__(self):
            super().__init__(name="weather_contract", _required_columns={MetricType.SINGLE_TURN: {"response", "reference"}})

        def init(self, run_config):
            pass  # Exact contract comparison needs no LLM/embedding.

        async def _single_turn_ascore(self, sample, callbacks):
            return float(sample.response == sample.reference)

    result = evaluate(EvaluationDataset(samples=[SingleTurnSample(response=encoded(r["actual"]),
                      reference=encoded(r["expected"])) for r in rows]), metrics=[WeatherContract()],
                      run_config=RunConfig(max_workers=1, max_retries=0), raise_exceptions=True, show_progress=False)
    return [r["weather_contract"] for r in result.scores]


def export():
    sys.path.insert(0, str(ROOT / "backend"))
    from llm.tools.weather import create_weather_tools
    tool = create_weather_tools()[0]
    rows = []
    for c in CASES:
        clock = datetime.fromisoformat("2026-10-13T" + c["now"] + ":00+09:00")

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(clock.timestamp(), tz or KST)

        with patch("llm.tools.weather.datetime", FrozenDateTime), patch(
                "travel.weather_service.get_stadium_weather", return_value=c["response"]) as service:
            try:
                actual = {"result": tool.invoke(c["input"])}
            except Exception as exc:
                actual = {"exception": type(exc).__name__}
            actual["calls"] = [list(call.args) for call in service.call_args_list]
        rows.append({"id": c["id"], "expected": c["expected"], "actual": actual})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "score", "scorer-check"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT="YES", DEEPEVAL_DISABLE_DOTENV="1",
                      DEEPEVAL_DISABLE_LEGACY_KEYFILE="1", PYTHON_DOTENV_DISABLED="1",
                      RAGAS_DO_NOT_TRACK="true", LANGSMITH_TRACING="false")

    def denied(*args, **kwargs):
        raise RuntimeError("Weather contract attempted network access")
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    if args.mode == "scorer-check":
        # Negative control is explicitly synthetic, not production evidence.
        rows = [{"id": "correct", "actual": None, "expected": None},
                {"id": "incorrect", "actual": "next hour", "expected": "last hour"}]
        assert deep_scores(rows) == ragas_scores(rows) == [1.0, 0.0]
        canonical = {"cases_sha256": hashlib.sha256(encoded(CASES).encode()).hexdigest(),
                     "results": [{"id": c["id"], "expected": c["expected"],
                                  "actual": c["expected"]} for c in CASES]}
        assert validated_rows(canonical) == canonical["results"]
        for change in ("expected", "hash", "id", "missing", "duplicate", "order", "malformed", "absent"):
            report = json.loads(encoded(canonical))
            if change == "expected":
                report["results"][0]["actual"] = {"exception": "AttributeError", "calls": []}
                report["results"][0]["expected"] = report["results"][0]["actual"]
            elif change == "hash":
                report["cases_sha256"] = "tampered"
            elif change == "id":
                report["results"][0]["id"] = "tampered"
            elif change == "missing":
                report["results"].pop()
            elif change == "duplicate":
                report["results"][1] = report["results"][0]
            elif change == "order":
                report["results"].reverse()
            elif change == "malformed":
                report["results"][0] = None
            else:
                del report["results"][0]["expected"]
            try:
                validated_rows(report)
            except ValueError:
                pass
            else:
                raise AssertionError("Accepted altered replay: " + change)
        return
    if args.output is None or args.output.exists():
        parser.error("Specify a new --output file")
    if args.mode == "export":
        rows = export()
        report = {"scope": __doc__, "cases_sha256": hashlib.sha256(encoded(CASES).encode()).hexdigest(),
                  "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "weather_sha256": hashlib.sha256((ROOT / "backend/llm/tools/weather.py").read_bytes()).hexdigest(),
                  "results": rows, "deepeval_version": version("deepeval"), "deepeval_scores": deep_scores(rows)}
        values = report["deepeval_scores"]
    else:
        report = json.loads(args.input.read_text())
        rows = validated_rows(report)
        report.update(ragas_version=version("ragas"), ragas_scores=ragas_scores(rows),
                      scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        values = report["ragas_scores"]
    if len(values) != len(rows) or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError("Missing or nonfinite framework scores")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(encoded({"mode": args.mode, "cases": len(values), "passed": sum(v == 1 for v in values)}))
    raise SystemExit(any(v != 1 for v in values))


if __name__ == "__main__":
    main()
