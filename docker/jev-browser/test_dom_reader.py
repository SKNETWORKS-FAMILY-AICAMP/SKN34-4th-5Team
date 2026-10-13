"""Real secure-container fixtures; URL/path supplied by the disposable harness."""
import asyncio
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@unittest.skipUnless(os.getenv("JEV_FIXTURE_BASE"), "requires controlled fixture server")
class DOMReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_frames_limits_and_private_redirect(self):
        cases = [("plain", "ok", "Actual article 14:00 ~ 15:00"), ("direct", "ok", "BEGIN"),
                 ("repeated", "ok", "ShopB"), ("race", "ok", "USABLE_RACE_ARTICLE"),
                 ("same", "ok", "ARTICLE_END"), ("aux", "ok", "MAP_ADDRESS"),
                 ("missing", "partial", "AVAILABLE_PARTIAL"), ("challenge", "blocked", ""),
                 ("short", "ok", "short valid"), ("lazy", "ok", "LAZY_END"),
                 ("overflow", "overflow", ""), ("redirect", "blocked", ""),
                 ("token-read", "ok", "Actual article 14:00 ~ 15:00"),
                 ("token-content", "ok", "Actual article 14:00 ~ 15:00"),
                 ("token-download", "ok", "Actual article 14:00 ~ 15:00")]
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + os.environ["JEV_MCP_TOKEN"]},
                                     timeout=90, trust_env=False) as http:
            async with streamable_http_client(os.environ["JEV_MCP_URL"], http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for path, status, needle in cases:
                        with self.subTest(path=path):
                            response = await session.call_tool("jev_read_body", {"url": os.environ["JEV_FIXTURE_BASE"] + path})
                            result = response.structuredContent or json.loads(response.content[0].text)
                            self.assertEqual(result["status"], status)
                            if needle:
                                self.assertIn(needle, result["body"])
                            self.assertNotIn("PRIVATE_MUST_NOT_REACH", result.get("body", ""))
                            if path == "repeated":
                                self.assertEqual(result["body"].count("Break 14:00 ~ 15:00"), 2)
                            if path == "direct":
                                self.assertTrue(all(part in result["body"] for part in ("BEGIN", "MIDDLE", "END")))
                            if path.startswith("token-"):
                                self.assertEqual(result["body"], "Article shell\n\nBEGIN\nActual article 14:00 ~ 15:00\nEND")
                                auxiliary = [frame for frame in result["frames"] if frame["chars"] == len("AUX_MUST_NOT_REACH")]
                                self.assertEqual(len(auxiliary), 8)
                                self.assertTrue(all(frame["role"] == "auxiliary" for frame in auxiliary))
                                self.assertNotIn("AUX_MUST_NOT_REACH", result["body"])


@unittest.skipUnless(os.getenv("JEV_READER_PATH"), "requires bundled reader and sandboxed Chrome")
class ChallengeReaderTests(unittest.TestCase):
    def test_mentions_are_articles_but_challenges_are_blocked(self):
        fixtures = {
            "/article": ("<title>Browser advice</title><article><h1>Browser advice</h1>"
                         "<p>Cloudflare can say verify you are human or Checking your browser.</p></article>", "ok"),
            "/leading-mention": ("<title>Browser advice</title><article>Verify you are human is a common prompt.</article>", "ok"),
            "/cloudflare": ("<title>Just a moment...</title><h1>example.test</h1>"
                            "<p>Verifying you are human. This may take a few seconds.</p>", "blocked"),
            "/heading": ("<title>example.test</title><p>example.test</p><h2>Verify you are human</h2>", "blocked"),
            "/checking": ("<title>example.test</title><h1>Checking your browser...</h1>", "blocked"),
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(fixtures.get(self.path, ("", ""))[0].encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for path, (_, status) in fixtures.items():
                    with self.subTest(path=path), tempfile.TemporaryDirectory() as profile:
                        output = subprocess.check_output(
                            ["node", os.environ["JEV_READER_PATH"],
                             f"http://127.0.0.1:{server.server_port}{path}", profile], text=True, timeout=50)
                        result = json.loads(output)
                        self.assertEqual(result["status"], status, result)
                        if status == "ok":
                            self.assertIn("human", result["body"])
                        else:
                            self.assertFalse(result.get("body"))
                        print(f"PASS real Chrome {path}: {status}", flush=True)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
