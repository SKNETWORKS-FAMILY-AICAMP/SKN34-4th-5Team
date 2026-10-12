r"""문서 검색 성능을 골든셋으로 잰다: 검색 방식(RAG_SEARCH_MODE)별 Hit@1·Hit@5·MRR (LLM 호출 0회, 질문 임베딩만).

  # 골든셋은 레포의 rag_test/golden 에 있다(컨테이너 밖) → 표준 입력으로 넘긴다
  docker compose exec -T backend python manage.py eval_retrieval --label rule  < rag_test\golden\golden_rule.jsonl
  docker compose exec -T backend python manage.py eval_retrieval --label venue < rag_test\golden\golden_venue.jsonl
  docker compose exec -T backend python manage.py eval_retrieval --label club  < rag_test\golden\golden_club.jsonl

  # 구장 안팎 장소 검색 경로(search_documents_tool)로 재기, RRF 상수 비교
  docker compose exec -T backend python manage.py eval_retrieval --label venue --path venue < rag_test\golden\golden_venue.jsonl
  docker compose exec -T backend python manage.py eval_retrieval --label rule --rrf-k 20,60,100 < rag_test\golden\golden_rule.jsonl

--path kbo  : search_kbo_documents 경로(규정·구장 문서). 구장·분류는 v1 router 규칙(rag_test STEP4 R3 와 같은 조건)
--path venue: search_documents_tool 경로(구장 안팎 장소). 구장·분류는 infer_slots, 검색어 변환(LLM)은 건너뛴다
문항별 결과 CSV: artifacts/rag_eval/
"""
import csv
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from llm.tools import knowledge


def rank_of(doc_ids, patterns):
    for i, doc_id in enumerate(doc_ids, 1):
        if any(re.fullmatch(p, doc_id or "") for p in patterns):
            return i
    return None


def summary(ranks, times):
    n = len(ranks) or 1
    return {"Hit@1": sum(1 for r in ranks if r == 1) / n, "Hit@5": sum(1 for r in ranks if r and r <= 5) / n,
            "MRR": sum(1 / r for r in ranks if r) / n, "P50ms": statistics.median(times) if times else 0}


class Command(BaseCommand):
    help = "골든셋으로 문서 검색 방식(vector·hybrid·hybrid_rerank)을 비교한다."

    def add_arguments(self, parser):
        parser.add_argument("--golden", default="-", help="골든셋 jsonl 경로, '-' 면 표준 입력")
        parser.add_argument("--label", default="golden")
        parser.add_argument("--modes", default="vector,hybrid,hybrid_rerank")
        parser.add_argument("-k", type=int, default=5)
        parser.add_argument("--path", choices=("kbo", "venue"), default="kbo")
        parser.add_argument("--rrf-k", default="", help="쉼표로 구분한 RRF 상수들 (예: 20,60,100). 하이브리드 방식마다 따로 잰다")

    def handle(self, *args, **o):
        from llm.v1.rag.club.retrieval import embed_many
        from llm.v1.rag.club.router import detect_categories, detect_stadium
        text = sys.stdin.read() if o["golden"] == "-" else Path(o["golden"]).read_text(encoding="utf-8")
        golden = [q for q in (json.loads(line) for line in text.splitlines() if line.strip()) if q.get("gold")]
        if not golden:
            raise CommandError("gold 가 있는 문항이 없습니다 (jsonl 을 표준 입력이나 --golden 으로 넘기세요)")
        from llm import sparse_index
        modes = [m for m in o["modes"].split(",") if m in knowledge.SEARCH_MODES]
        rrf_ks = [int(v) for v in o["rrf_k"].split(",") if v.strip()] or [sparse_index.RRF_K]
        runs = [(m, None) if m == "vector" else (m, rk) for m in modes for rk in ([None] if m == "vector" else rrf_ks)]
        vectors = embed_many([q["question"] for q in golden])
        rows, results = [], {}
        for mode, rrf_k in runs:
            name = mode if rrf_k is None or len(rrf_ks) == 1 else f"{mode}@k{rrf_k}"
            if rrf_k is not None:
                sparse_index.RRF_K = rrf_k
            ranks, times = [], []
            for q, vec in zip(golden, vectors):
                started = time.perf_counter()
                if o["path"] == "venue":
                    docs, _ = knowledge.retrieve_venue(q["question"], knowledge.infer_slots(q["question"]), mode=mode, qvec=vec)
                    ids = [(d.get("metadata") or {}).get("doc_id") for d in docs[:o["k"]]]
                else:
                    hits = knowledge.search_kbo_rows(q["question"], detect_stadium(q["question"]),
                                                     detect_categories(q["question"]) or None, o["k"],
                                                     _embed=lambda _text, vec=vec: vec, mode=mode)
                    ids = [h.get("doc_id") for h in hits]
                times.append((time.perf_counter() - started) * 1000)
                rank = rank_of(ids, q["gold"])
                ranks.append(rank)
                rows.append({"mode": name, "id": q["id"], "group": q.get("group", ""), "question": q["question"],
                             "rank": rank or "miss", "top": " | ".join(map(str, ids))})
            results[name] = (summary(ranks, times), ranks)
        self.stdout.write(f"[{o['label']}] {len(golden)}문항 · 경로={o['path']} · k={o['k']}")
        self.stdout.write("| 방식 | Hit@1 | Hit@5 | MRR | P50ms |\n|---|---|---|---|---|")
        for mode, (s, _) in results.items():
            self.stdout.write(f"| {mode} | {s['Hit@1']:.3f} | {s['Hit@5']:.3f} | {s['MRR']:.3f} | {s['P50ms']:.0f} |")
        if "vector" in results:
            base = results["vector"][1]
            for mode, (_, ranks) in results.items():
                if mode == "vector":
                    continue
                better = [q["id"] for q, a, b in zip(golden, base, ranks) if (b or 99) < (a or 99)]
                worse = [q["id"] for q, a, b in zip(golden, base, ranks) if (b or 99) > (a or 99)]
                self.stdout.write(f"{mode} vs vector: 좋아짐 {better or '-'} / 나빠짐 {worse or '-'}")
        out = Path(settings.BASE_DIR) / "artifacts" / "rag_eval"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{o['label']}_{datetime.now():%m%d_%H%M}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        self.stdout.write(f"문항별 결과: {path}")
