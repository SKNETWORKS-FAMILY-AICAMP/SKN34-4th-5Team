"""도구 결과가 근거 등급과 실제 출처를 직접 알려 주는지 확인한다 (10/14 개발 서버 QA)."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from baseball.stadium_guides import parking_map
from llm.tools.baseball import _tving_evidence
from llm.v2.middleware.jev_guidelines import GROUNDING_RULES


class EvidenceGradeTests(SimpleTestCase):
    def test_tving_rows_are_third_party_with_real_source(self):
        rows = [{"source": "tving"}, {"source": "tving"}]
        self.assertEqual(_tving_evidence(rows, "https://www.tving.com/sports/kbo"),
                         {"evidenceGrade": "THIRD_PARTY", "sourceName": "TVING", "sourceUrl": "https://www.tving.com/sports/kbo"})

    def test_mixed_or_unknown_rows_are_unconfirmed_without_link(self):
        self.assertEqual(_tving_evidence([{"source": "tving"}, {"source": "csv"}], "u"), {"evidenceGrade": "UNCERTAIN"})
        self.assertEqual(_tving_evidence([], "u"), {})

    def _stadium(self, credit, source_url):
        fields = dict(url="/p.png", source_url=source_url, credit=credit, title=None, summary=None, kind=None,
                      visual_notes=None, captured_at=None, width=None, height=None)
        return SimpleNamespace(**{f"parking_map_{k}": v for k, v in fields.items()})

    def test_parking_map_from_user_report_site_is_unofficial(self):
        result = parking_map(self._stadium("자리어때(myseatcheck.com)", "https://myseatcheck.com/x"))
        self.assertEqual(result["evidenceGrade"], "UNOFFICIAL")
        self.assertNotIn("evidenceGrade", parking_map(self._stadium("구단 공식", "https://example.org/p")))

    def test_rules_cover_lookup_failure_and_grade_choice(self):
        self.assertIn("일반 상식으로 허용·금지·수치를 단정하지 않는다", GROUNDING_RULES)
        self.assertIn("evidenceGrade", GROUNDING_RULES)
        self.assertIn("출처 링크가 없다는 말을 덧붙이지 않는다", GROUNDING_RULES)
