"""Offline YouTube transport contracts; no provider quality claims."""
import base64
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from llm.tests.test_highlight_langchain import SDKTransport, canonical_part

spec = importlib.util.spec_from_file_location("youtube_contract", os.environ.get(
    "HIGHLIGHT_CONTRACT_SOURCE", str(Path(__file__).parents[1] / "highlight_multimodal.py")))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)
ROW = {"id": "candidate", "video_id": "SlvDBmr4Bd8", "start": 120.125, "end": 140.375,
       "url": "https://untrusted.invalid/ignored"}
OUTPUT = {"candidate_id": "candidate", "match": True, "original_seconds": 123.25,
          "source_accessible": True, "evidence": "visible bat contact"}
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/webp;base64," + base64.b64encode(b"original-image").decode()}}


def google(output=OUTPUT):
    client = mm.Google("synthetic", quota=Mock())
    client._transport = SDKTransport(output)
    client._transport.__enter__()
    return client


def verify(client, row=ROW, images=None):
    return client.verify("find the play", row, "/nonexistent/clip.mp4", "/nonexistent/clip.wav", images=images)


class YouTubeVerifierTests(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def test_url_offsets_parity_no_local_media_and_original_images(self):
        client = google()
        with patch.object(mm, "media", side_effect=AssertionError("local media read")), \
             patch.object(mm.Path, "read_bytes", side_effect=AssertionError("local bytes read")):
            result = verify(client, images=[IMAGE])
        count, generate = [body for _, body in client._transport.requests]
        self.assertEqual(count["contents"], generate["contents"])
        parts = generate["contents"][0]["parts"]
        self.assertEqual(canonical_part(parts[1]), canonical_part({"fileData": {"mimeType": "video/mp4",
            "fileUri": "https://www.youtube.com/watch?v=SlvDBmr4Bd8"},
            "videoMetadata": {"startOffset": "120.125s", "endOffset": "140.375s"}}))
        self.assertEqual([canonical_part(p) for p in parts[2:]], [canonical_part(p) for p in mm.image_parts([IMAGE], embedding=False)])
        self.assertNotIn("untrusted.invalid", json.dumps(generate))
        self.assertNotIn("AGENTIC", json.dumps(generate))
        self.assertIn("ORIGINAL-video", parts[0]["text"])
        self.assertIn("source_accessible", generate["generationConfig"]["responseJsonSchema"]["required"])
        self.assertEqual(result, {"candidate_id": "candidate", "match": True,
            "local_seconds": 3.125, "evidence": "visible bat contact"})
        self.assertEqual(mm.decision(result, ROW)["playback_seconds"], 123.25)
        client.quota.acquire.assert_called_once_with(client.model, 9)

    def test_text_transport_and_confirmed_negative(self):
        client = google({**OUTPUT, "match": False, "original_seconds": ROW["start"]})
        result = verify(client)
        self.assertIsNone(mm.decision(result, ROW))
        self.assertEqual(result["local_seconds"], 0)
        self.assertEqual(len(client._transport.requests[1][1]["contents"][0]["parts"]), 2)

    def test_invalid_metadata_rejected_before_calls(self):
        defects = [{"video_id": value} for value in (None, 123, "short", "SlvDBmr4Bd8\n", "../evilxxxx", "https://youtu.be/SlvDBmr4Bd8")]
        defects += [{key: value} for key in ("start", "end") for value in (None, True, "120", float("nan"), float("inf"))]
        defects += [{"start": -1}, {"end": ROW["start"]}, {"end": 0}]
        for defect in defects:
            with self.subTest(defect=defect):
                client = google()
                with self.assertRaises(ValueError):
                    verify(client, {**ROW, **defect})
                self.assertEqual(client._transport.requests, [])
                client.quota.acquire.assert_not_called()
        for missing in ("video_id", "start", "end", "id"):
            row = dict(ROW)
            del row[missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                verify(google(), row)

    def test_bad_original_time_binding_and_evidence_rejected(self):
        defects = [{"original_seconds": value} for value in (120, ROW["end"], 141, True, None, "123", float("nan"), float("inf"))]
        defects += [{"candidate_id": "wrong"}, {"match": "true"}, {"match": 1}, {"evidence": ""}, {"evidence": "x" * 2001}]
        defects += [{"source_accessible": value} for value in (False, None, "true", 1)]
        for defect in defects:
            with self.subTest(defect=defect), self.assertRaises((ValueError, RuntimeError)):
                verify(google({**OUTPUT, **defect}))
        for missing in OUTPUT:
            output = dict(OUTPUT)
            del output[missing]
            with self.subTest(missing=missing), self.assertRaises((ValueError, RuntimeError)):
                verify(google(output))

    def test_malformed_provider_output_rejected(self):
        for output in (None, [], "text", {"local_seconds": 3, "match": True}):
            with self.subTest(output=output), self.assertRaises(ValueError):
                verify(google(output))
        for response in ({}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]}):
            client = google()
            client._transport.response = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                verify(client)

    def test_google_errors_and_unreadable_are_incomplete_in_both_callers(self):
        for images in (None, [IMAGE]):
            for output in ({**OUTPUT, "source_accessible": False, "match": False},
                           {**OUTPUT, "candidate_id": "wrong"}, None, "provider-error"):
                with self.subTest(images=bool(images), output=output):
                    client = google(output)
                    if output == "provider-error":
                        client.call = Mock(side_effect=RuntimeError("Google countTokens network failure"))
                    specialist = object.__new__(mm.Specialist)
                    specialist.root, specialist.rows = Path("/nonexistent"), {ROW["id"]: ROW}
                    specialist.google, specialist.verifier = Mock(), client
                    specialist.google.embed.return_value = [1.] * mm.DIM
                    specialist.db, specialist.sql = Mock(), Mock()
                    specialist.db.query_points_groups.return_value.groups = [SimpleNamespace(id=ROW['video_id'], hits=[SimpleNamespace(id=ROW['id'], payload=ROW)])]
                    specialist.sources = {ROW['video_id']: {'duration': ROW['end']}}
                    specialist.sql.execute.return_value = []
                    # Invalid synthetic WebP stays original for verification; retrieval helper is independent.
                    with patch.object(mm, "image_parts", wraps=lambda blocks, **kw: [] if not blocks else [{"inlineData": {"mimeType": "image/webp", "data": IMAGE["image_url"]["url"].split(",")[1]}}]):
                        result = specialist.ask("find the play", images=images)
                    self.assertFalse(result["complete"])
                    self.assertIsNone(result["no_match"])
                    self.assertEqual(result["matches"], [])

    def test_subsecond_window_is_not_rounded_or_minimum_filtered(self):
        row = {**ROW, "start": 0.000125, "end": 0.125375}
        client = google({**OUTPUT, "original_seconds": 0.00125})
        result = verify(client, row)
        self.assertAlmostEqual(result["local_seconds"], 0.001125)
        part = client._transport.requests[1][1]["contents"][0]["parts"][1]
        self.assertEqual(canonical_part(part)["video_metadata"], {"start_offset": "0.000125s", "end_offset": "0.125375s"})


if __name__ == "__main__":
    unittest.main()
