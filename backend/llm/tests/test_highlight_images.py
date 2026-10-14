"""Synthetic request contracts, not live image/retrieval quality evidence."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from llm.tests.test_highlight_langchain import SDKTransport, canonical_part

spec = importlib.util.spec_from_file_location("image_contract", os.environ.get(
    "HIGHLIGHT_CONTRACT_SOURCE", str(Path(__file__).parents[1] / "highlight_multimodal.py")))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"original-image").decode()}}


class ImageContractTests(unittest.TestCase):
    def test_image_query_is_one_embedding_request(self):
        google = mm.Google("synthetic")
        google.call = Mock(return_value={"embedding": {"values": [1.] * mm.DIM}})
        parts = [{"text": "same scene"}, *mm.image_parts([IMAGE])]
        self.assertEqual(google.embed(parts), [1.] * mm.DIM)
        google.call.assert_called_once_with("gemini-embedding-2", "embedContent",
            {"content": {"parts": parts}, "outputDimensionality": mm.DIM})

    def test_image_bytes_and_url_rejection(self):
        self.assertEqual(mm.image_parts([IMAGE]), [{"inlineData": {"mimeType": "image/png",
            "data": base64.b64encode(b"original-image").decode()}}])
        for url in ("https://example.com/image.png", "file:///tmp/image", "data:image/png;base64,!!!", "data:text/plain;base64,YQ==", "data:image/gif;base64,YQ=="):
            with self.assertRaises(ValueError):
                mm.image_parts([{"type": "image_url", "image_url": {"url": url}}])

    def test_original_image_in_count_and_verifier(self):
        google = mm.Google("synthetic", quota=Mock())
        output = {"candidate_id": "x", "match": False, "original_seconds": 0,
                  "source_accessible": True, "evidence": "different play"}
        with SDKTransport(output) as transport, patch.object(mm, "media", side_effect=AssertionError("verification must not read media")):
            google.verify("same scene", {"id": "x", "video_id": "SlvDBmr4Bd8", "start": 0, "end": 24}, "clip", "audio", images=[IMAGE])
        count_body, verify_body = [body for _, body in transport.requests]
        self.assertEqual(count_body["contents"], verify_body["contents"])
        self.assertEqual(canonical_part(verify_body["contents"][0]["parts"][-1]), canonical_part(mm.image_parts([IMAGE])[0]))


    def test_image_query_reaches_video_search_and_all_candidates_without_output_leak(self):
        from types import SimpleNamespace
        specialist = object.__new__(mm.Specialist)
        specialist.google, specialist.verifier, specialist.db, specialist.sql = Mock(), Mock(), Mock(), Mock()
        specialist.google.embed.return_value = [1.] * mm.DIM
        specialist.verifier.last_model_version = "synthetic"
        specialist.root = Path("/synthetic")
        specialist.rows = {"x": {"id": "x", "video_id": "SlvDBmr4Bd8", "start": 0, "end": 24}}
        specialist.db.query_points_groups.return_value.groups = [SimpleNamespace(hits=[SimpleNamespace(id='x', payload=specialist.rows['x'])])]
        specialist.sql.execute.return_value = [("x",)]
        specialist.verifier.verify.return_value = {"candidate_id": "x", "match": False, "local_seconds": 0, "evidence": "absent"}
        result = specialist.ask("same scene", images=[IMAGE])
        specialist.google.embed.assert_called_once_with([{"text": "same scene"}, *mm.image_parts([IMAGE])])
        specialist.db.query_points_groups.assert_called_once_with(mm.COLLECTION,
            query=[1.] * mm.DIM, using='video', group_by='video_id', group_size=1, limit=3)
        specialist.sql.execute.assert_not_called()
        self.assertEqual(result["rankings"], {"video": ["x"]})
        self.assertEqual(result["retrieval_only"], ["x"])
        self.assertEqual(specialist.verifier.verify.call_args.kwargs, {"images": [IMAGE]})
        self.assertNotIn("original-image", json.dumps(result))
        self.assertNotIn("base64", json.dumps(result))
        self.assertTrue(result["no_match"])
        specialist.ask("text only")
        specialist.google.embed.assert_called_with({"text": "task: search result | query: text only"})
        self.assertEqual(specialist.verifier.verify.call_args.kwargs, {})
        specialist.verifier.verify.side_effect = RuntimeError("provider unavailable")
        failed = specialist.ask("same scene", images=[IMAGE])
        self.assertFalse(failed["complete"])
        self.assertIsNone(failed["no_match"])

    def test_supported_six_webp_and_explicit_overflow(self):
        import io
        from PIL import Image
        self.assertEqual(len(mm.image_parts([IMAGE] * 6)), 6)
        for count in (7, 10):
            with self.assertRaisesRegex(ValueError, "최대 6개"):
                mm.image_parts([IMAGE] * count)
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, format="WEBP", lossless=True)
        block = {"type": "image_url", "image_url": {"url": "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode()}}
        part = mm.image_parts([block])[0]["inlineData"]
        self.assertEqual(part["mimeType"], "image/png")
        with Image.open(io.BytesIO(base64.b64decode(part["data"]))) as converted:
            self.assertEqual(converted.getpixel((0, 0)), (255, 0, 0))

    def test_constructor_closes_clients_on_segments_and_sql_failure(self):
        import sqlite3
        from qdrant_client import QdrantClient
        for sql_failure in (False, True):
            clients = []
            def capture(**kwargs):
                client = QdrantClient(**kwargs)
                clients.append(client)
                return client
            with tempfile.TemporaryDirectory() as root:
                sqlite3.connect(Path(root) / "transcripts.sqlite3").close()
                (Path(root) / "segments.json").write_text("{broken")
                (Path(root) / "qdrant").mkdir()
                with patch("qdrant_client.QdrantClient", side_effect=capture):
                    if sql_failure:
                        with patch.object(mm.sqlite3, "connect", side_effect=sqlite3.OperationalError("open")), self.assertRaises(sqlite3.Error):
                            mm.Specialist(root, None)
                    else:
                        with self.assertRaises(ValueError):
                            mm.Specialist(root, None)
                self.assertTrue(clients[0]._client._closed)
                clients[0].close()


    def test_reopen_rejects_arbitrary_extra_collection(self):
        from qdrant_client import QdrantClient, models
        for mode in ("separate",):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                seed = mm.Specialist(root, Mock(), ingesting=True)
                sid = "12345678-1234-1234-1234-123456789012"
                row = {"id": sid, "video_id": "SlvDBmr4Bd8", "start": 0, "end": 24, "text": "caption"}
                collection = mm.COLLECTION
                config = models.VectorParams(size=mm.DIM, distance=models.Distance.COSINE)
                seed.db.create_collection(collection, vectors_config={m: config for m in mm.MODALITIES})
                seed.db.upsert(collection, [models.PointStruct(id=sid, payload=row,
                    vector={m: [1.] * mm.DIM for m in mm.MODALITIES})])
                seed.sql.execute("INSERT INTO transcript VALUES (?, ?)", (sid, "caption"))
                seed.sql.commit()
                seed.close()
                (root / "index_mode.json").write_text(json.dumps(mode))
                (root / "segments.json").write_text(json.dumps({sid: row}))
                (root / "clips").mkdir()
                for suffix in (".mp4", ".wav"):
                    (root / "clips" / (sid + suffix)).write_bytes(b"synthetic")
                google = Mock()
                opened = mm.Specialist(root, google)
                opened.close()  # The same fixture is ready before the extra collection.
                client = QdrantClient(path=str(root / "qdrant"))
                client.create_collection("unexpected_stored_layout", vectors_config=config)
                client.close()
                with self.assertRaisesRegex(ValueError, "corpus layout"):
                    opened = mm.Specialist(root, google)
                    opened.close()
                client = QdrantClient(path=str(root / "qdrant"))
                try:
                    self.assertEqual({c.name for c in client.get_collections().collections},
                        {collection, "unexpected_stored_layout"})
                    self.assertEqual(client.count(collection).count, 1)
                finally:
                    client.close()
                self.assertEqual(google.mock_calls, [])

    def test_reopen_rejects_vector_identity_payload_transcript_and_missing_media(self):
        from qdrant_client import models
        for defect in ("vector", "identity", "payload", "transcript", "media", "empty"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                seed = mm.Specialist(root, Mock(), ingesting=True)
                sid = "12345678-1234-1234-1234-123456789012"
                row = {"id": sid, "video_id": "SlvDBmr4Bd8", "start": 0, "end": 24, "text": "caption"}
                size = 3 if defect == "vector" else mm.DIM
                seed.db.create_collection(mm.COLLECTION, vectors_config={m: models.VectorParams(size=size, distance=models.Distance.COSINE) for m in mm.MODALITIES})
                if defect != "empty":
                    seed.db.upsert(mm.COLLECTION, [models.PointStruct(id=sid, vector={m: [1.] * size for m in mm.MODALITIES},
                        payload={**row, "text": "wrong"} if defect == "payload" else row)])
                seed.sql.execute("INSERT INTO transcript VALUES (?, ?)", (sid, "wrong" if defect == "transcript" else "caption"))
                seed.sql.commit()
                seed.close()
                (root / "index_mode.json").write_text(json.dumps("separate"))
                (root / "segments.json").write_text(json.dumps({"wrong": row} if defect == "identity" else {sid: row}))
                (root / "clips").mkdir()
                if defect != "media":
                    for suffix in (".mp4", ".wav"):
                        (root / "clips" / (sid + suffix)).write_bytes(b"synthetic")
                with self.assertRaises(ValueError):
                    opened = mm.Specialist(root, Mock(), audit=True)
                    opened.close()

    def test_trusted_text_harness_uses_exact_old_api_and_binds_input(self):
        import sys
        import subprocess
        import hashlib
        sys.path.insert(0, str(Path(__file__).parents[2]))
        from llm.v2.evaluation import highlight_deepeval as hd
        source = subprocess.check_output(["git", "show", "35d0b794bfa6ffcd4d806e7c31106f0bbd8e09cd:backend/llm/highlight_multimodal.py"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline_path = root / "baseline.py"
            baseline_path.write_bytes(source)
            old_spec = importlib.util.spec_from_file_location("llm.highlight_multimodal", baseline_path)
            baseline = importlib.util.module_from_spec(old_spec)
            old_spec.loader.exec_module(baseline)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "web.lock").touch()
            (corpus / "segments.json").write_text("{}")
            path = root / "cases.json"
            path.write_text(json.dumps({"corpus_segments": 0, "cases": [{"id": str(i), "query": "original", "references": []} for i in range(8)]}))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            negative = {"complete": True, "no_match": True, "retrieval_only": [], "decisions": [], "matches": []}
            for mutation in (False, True):
                output = root / ("changed" if mutation else "stable")
                argv = ["highlight", "--dataset", str(path), "--corpus", str(corpus), "--output", str(output), "--run-provider"]
                def ask(*args, **kwargs):
                    if mutation:
                        path.write_text(path.read_text() + " ")
                    return dict(negative)
                # Preserve old production API/source; only transport/result boundary is scripted.
                with patch.dict(sys.modules, {"llm.highlight_multimodal": baseline}), patch.object(sys, "argv", argv), \
                     patch.object(baseline, "Google"), patch.object(baseline.Specialist, "ask", side_effect=ask), \
                     patch.object(hd.time, "sleep"), patch.object(hd, "evaluate", return_value=Mock(model_dump=lambda **kw: {})):
                    if mutation:
                        with self.assertRaisesRegex(ValueError, "Dataset changed"):
                            hd.main()
                        self.assertFalse((output / "summary.json").exists())
                    else:
                        hd.main()
                        summary = json.loads((output / "summary.json").read_text())
                        self.assertEqual(summary["dataset_sha256"], digest)
                        self.assertEqual(summary["index_mode"], "separate")

    def test_result_scored_by_both_frameworks(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parents[2]))
        from llm.v2.evaluation.highlight_deepeval import CorpusMetric
        from llm.v2.evaluation.highlight_ragas import CorpusRagasMetric
        from deepeval.test_case import LLMTestCase
        from ragas import SingleTurnSample
        label = {"references": []}
        result = {"complete": True, "no_match": True, "retrieval_only": [], "decisions": [], "matches": [], "candidate_rows": {}}
        row = {"id": "x", "video_id": "SlvDBmr4Bd8", "start": 0, "end": 24}
        output = {"candidate_id": "x", "match": True, "local_seconds": 1, "evidence": "wrong event"}
        wrong = {"complete": True, "no_match": False, "retrieval_only": ["x"],
                 "decisions": [{"candidate_id": "x", "output": output}],
                 "matches": [mm.decision(output, row)], "candidate_rows": {"x": row}}
        for actual, expected in ((result, 1.), (wrong, 0.)):
            self.assertEqual(CorpusMetric("no_match").measure(LLMTestCase(input="synthetic absent",
                expected_output=json.dumps(label), actual_output=json.dumps(actual))), expected)
            self.assertEqual(CorpusRagasMetric("no_match").single_turn_score(
                SingleTurnSample(response=json.dumps(actual), reference=json.dumps(label))), expected)
        from llm.v2.evaluation.highlight_deepeval import scores
        self.assertIsNone(scores(label, {**result, "complete": False, "no_match": None})["no_match"])


if __name__ == "__main__":
    unittest.main()
