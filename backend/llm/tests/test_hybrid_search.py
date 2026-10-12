"""하이브리드 문서 검색(벡터 + 한국어 키워드 BM25, RRF) 테스트. Qdrant 는 메모리 모드, 임베딩은 가짜 벡터."""
from unittest.mock import patch

from django.test import SimpleTestCase
from qdrant_client import QdrantClient

from llm import sparse_index, vector_store
from llm.tools import knowledge


def vec(*hot):
    """1536 차원 가짜 임베딩: hot 위치만 1."""
    v = [0.0] * vector_store.DOCUMENT_DIMENSION
    for i in hot:
        v[i] = 1.0
    return v


DOCS = [
    {"id": "RULE_COMMON_BR_6.02", "content": "6.02 투수의 반칙행위(보크): 주자가 있을 때 투수판에 중심발을 두고 투구 동작을 멈추면 보크가 선언된다.",
     "metadata": {"doc_id": "RULE_COMMON_BR_6.02", "category": "RULE"}, "embedding": vec(1)},
    {"id": "RULE_COMMON_BR_5.09", "content": "5.09 인필드 플라이: 무사 또는 1사에 주자 1·2루나 만루일 때 내야 뜬공.",
     "metadata": {"doc_id": "RULE_COMMON_BR_5.09", "category": "RULE"}, "embedding": vec(2)},
    {"id": "SEAT_JAMSIL_1", "content": "잠실 1루 응원석 좌석 안내와 가격.",
     "metadata": {"doc_id": "SEAT_JAMSIL_1", "category": "SEAT", "stadium_code": "JAMSIL"}, "embedding": vec(3)},
]


class TokenTest(SimpleTestCase):
    def test_korean_tokens_strip_josa_and_add_bigrams(self):
        toks = sparse_index.tokens("보크가 뭐야? 인필드플라이는 5.09 조항 알려줘")
        self.assertIn("w:보크", toks)                  # 조사 '가' 제거
        self.assertIn("w:인필드플라이", toks)          # 붙여 쓴 말도 낱말로
        self.assertIn("b:필드", toks)                  # 2-gram 으로 '인필드 플라이' 띄어 쓴 문서와도 맞는다
        self.assertIn("w:5.09", toks)                  # 조항 번호
        self.assertNotIn("w:뭐야", toks)
        self.assertNotIn("w:알려줘", toks)

    def test_rrf_merges_by_doc_id(self):
        fused = sparse_index.rrf([{"doc_id": "a"}, {"doc_id": "b"}], [{"doc_id": "b"}, {"doc_id": "c"}])
        self.assertEqual([r["doc_id"] for r in fused], ["b", "a", "c"])  # 두 목록에 다 있는 b 가 1위
        self.assertGreater(fused[0]["rrf"], fused[1]["rrf"])


class HybridSearchTest(SimpleTestCase):
    def setUp(self):
        self.db = QdrantClient(":memory:")
        patcher = patch("llm.vector_store.client", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        vector_store.upsert_documents(DOCS)  # sparse 컬렉션이 아직 없으면 키워드 색인은 건너뛴다

    def test_sparse_index_is_built_from_documents_and_filtered(self):
        self.assertFalse(sparse_index.exists())
        done, average = sparse_index.build_from_documents(report=lambda _: None)
        self.assertEqual(done, 3)
        self.assertGreater(average, 0)
        hits = sparse_index.search("보크 선언 기준", categories=["RULE"])
        self.assertEqual(hits[0]["metadata"]["doc_id"], "RULE_COMMON_BR_6.02")
        self.assertFalse(sparse_index.search("좌석", categories=["RULE"]))  # 분류 필터는 벡터 검색과 같다

    def test_new_documents_follow_into_sparse_index(self):
        sparse_index.build_from_documents(report=lambda _: None)
        vector_store.upsert_documents([{"id": "RULE_X", "content": "타임 선언은 심판이 한다.",
                                        "metadata": {"doc_id": "RULE_X", "category": "RULE"}, "embedding": vec(4)}])
        self.assertEqual(sparse_index.search("타임 선언")[0]["metadata"]["doc_id"], "RULE_X")

    def test_hybrid_brings_keyword_match_that_vector_ranks_low(self):
        sparse_index.build_from_documents(report=lambda _: None)
        query = "보크 언제 선언돼?"
        wrong = vec(2)  # 벡터는 인필드 플라이 문서 쪽을 가리킨다
        vector_only = knowledge.search_kbo_rows(query, None, ["RULE"], 1, _embed=lambda _: wrong, mode="vector")
        hybrid = knowledge.search_kbo_rows(query, None, ["RULE"], 1, _embed=lambda _: wrong, mode="hybrid")
        self.assertEqual(vector_only[0]["doc_id"], "RULE_COMMON_BR_5.09")  # 이전 방식: 벡터가 틀리면 1위도 틀린다
        self.assertEqual(hybrid[0]["doc_id"], "RULE_COMMON_BR_6.02")       # 키워드(보크·선언)가 맞는 문서가 1위
        self.assertIn("rrf", hybrid[0])

    def test_without_sparse_index_falls_back_to_vector(self):
        rows = knowledge.search_kbo_rows("보크", None, ["RULE"], 2, _embed=lambda _: vec(1), mode="hybrid")
        self.assertEqual(rows[0]["doc_id"], "RULE_COMMON_BR_6.02")
        self.assertNotIn("rrf", rows[0])

    def test_mode_env(self):
        with patch.dict("os.environ", {"RAG_SEARCH_MODE": "VECTOR"}):
            self.assertEqual(knowledge.search_mode(), "vector")
        with patch.dict("os.environ", {"RAG_SEARCH_MODE": "이상한값"}):
            self.assertEqual(knowledge.search_mode(), "hybrid")


class EvalHelpersTest(SimpleTestCase):
    def test_rank_and_summary(self):
        from llm.management.commands.eval_retrieval import rank_of, summary
        self.assertEqual(rank_of(["X", "RULE_COMMON_BR_6.02_p2"], [r"RULE_COMMON_BR_6\.02(_p\d+)?"]), 2)
        self.assertIsNone(rank_of(["X"], [r"Y"]))
        s = summary([1, 2, None, 6], [10, 20, 30, 40])
        self.assertEqual((s["Hit@1"], s["Hit@5"]), (0.25, 0.5))
        self.assertAlmostEqual(s["MRR"], (1 + 0.5 + 1 / 6) / 4)


class VenueHybridTest(SimpleTestCase):
    def setUp(self):
        self.db = QdrantClient(":memory:")
        patcher = patch("llm.vector_store.client", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        docs = [{"id": "FAC_JAMSIL_수유실", "content": "잠실 수유실은 1루 2층 복도 끝에 있다.",
                 "metadata": {"doc_id": "FAC_JAMSIL_수유실", "category": "FACILITY", "stadium_code": "JAMSIL"}, "embedding": vec(5)}]
        docs += [{"id": f"FAC_JAMSIL_화장실{i}", "content": f"잠실 화장실 {i}번 위치 안내.",
                  "metadata": {"doc_id": f"FAC_JAMSIL_화장실{i}", "category": "FACILITY", "stadium_code": "JAMSIL"},
                  "embedding": vec(6, 10 + i)} for i in range(3)]
        vector_store.upsert_documents(docs)

    def test_venue_search_adds_keyword_match_that_vector_missed(self):
        slots = {"stadium_code": "JAMSIL", "categories": ["FACILITY"]}
        docs, method = knowledge.retrieve_venue("수유실 어디야", slots, mode="vector", qvec=vec(6))
        self.assertEqual(method, "vector")
        self.assertNotIn("FAC_JAMSIL_수유실", [d["metadata"]["doc_id"] for d in docs])  # 벡터만: 화장실 문서 3개뿐
        sparse_index.build_from_documents(report=lambda _: None)
        docs, method = knowledge.retrieve_venue("수유실 어디야", slots, mode="hybrid", qvec=vec(6))
        self.assertEqual(method, "hybrid")
        self.assertIn("FAC_JAMSIL_수유실", [d["metadata"]["doc_id"] for d in docs[:2]])


class ScoreFusionTest(SimpleTestCase):
    setUp = HybridSearchTest.setUp

    def test_score_fusion_rescues_keyword_candidate_outside_vector_top(self):
        sparse_index.build_from_documents(report=lambda _: None)
        wrong = vec(2)
        with patch("llm.v1.rag.club.retrieval.search", return_value=([vector_store.legacy_row(
                {**vector_store.search(wrong, k=1, categories=["RULE"])[0]})], 1.0)):  # 벡터 후보가 1개뿐인 상황
            rows = knowledge.search_kbo_rows("보크 언제 선언돼?", None, ["RULE"], 2, _embed=lambda _: wrong, mode="hybrid_score")
        ids = [r["doc_id"] for r in rows]
        self.assertIn("RULE_COMMON_BR_6.02", ids)   # 벡터 후보에 없던 보크 조항을 키워드로 데려와 벡터 거리까지 붙여 비교
        self.assertTrue(all(r["dist"] is not None for r in rows))


class RuleRerankTest(SimpleTestCase):
    def test_title_exact_match_separates_twin_articles(self):
        rows = [  # 벡터·키워드 모두 PO 조항을 조금 더 위로 본 상황(RRF 점수 차이는 작다)
            {"doc_id": "LR_제38조", "rrf": 0.0330, "status": "CONFIRMED",
             "content": "[KBO 리그규정 2026] 제5장 포스트시즌 > 제38조 플레이오프\n플레이오프는 5전 3선승제로 한다."},
            {"doc_id": "LR_제34조", "rrf": 0.0325, "status": "CONFIRMED",
             "content": "[KBO 리그규정 2026] 제5장 포스트시즌 > 제34조 준플레이오프\n준플레이오프는 5전 3선승제로 한다."},
        ]
        ranked = knowledge.rule_rerank("준플레이오프는 몇 선승제야?", rows)
        self.assertEqual(ranked[0]["doc_id"], "LR_제34조")  # 제목에 '준플레이오프'가 정확히 있는 조항이 1위
