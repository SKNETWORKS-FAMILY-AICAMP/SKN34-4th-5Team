"""Real secure-container fixtures; URL/path supplied by the disposable harness."""
import asyncio
import json
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
