"""Direct NOL MCP contract, without network/model calls."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
from test_chrome_lodging import server

URL = "https://nol.yanolja.com/stay/domestic/3012752"


class DirectToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.lock = asyncio.Lock()
        server.warm_task = None

    async def test_registry_and_url_boundary(self):
        tools = {t.name: t for t in await server.mcp.list_tools()}
        self.assertIn("jev_read_lodging", tools)
        self.assertEqual(set(tools["jev_read_lodging"].inputSchema["properties"]), {"url"})
        invalid = [URL.replace("https:", "http:"), URL.replace("nol.", "evil."),
                   URL.replace("nol.yanolja.com", "nol.yanolja.com.evil.test"),
                   URL.replace("//", "//user:pass@"), URL.replace(".com/", ".com:443/"),
                   URL + "/", URL + "#fragment", URL + "?foo=bar", URL + "?verticalCategory=OTHER",
                   URL.replace("3012752", "../3012752"), URL + "\n", "file:///etc/passwd"]
        with patch.object(server, "_chrome_lodging", AsyncMock(return_value={"status": "read"})) as collect:
            for url in invalid:
                self.assertEqual(await server.jev_read_lodging(url), {"status": "blocked"}, url)
            collect.assert_not_awaited()
            for url in (URL, URL + "?verticalCategory=PRODUCT_CATEGORY_KOREA_ACCOMMODATION"):
                response = await server.mcp.call_tool("jev_read_lodging", {"url": url})
                content = response[0] if isinstance(response, tuple) else response
                self.assertEqual(json.loads(content[0].text), {"status": "read"})
                collect.assert_awaited_with("", 45, url)

    async def test_status_cleanup_and_credentials(self):
        for status in ("read", "blocked", "partial", "error", "timeout"):
            process = AsyncMock(pid=99999999, returncode=0)
            process.stdout.read.return_value = b""
            output = {"status": status}
            if status == "read":
                output.update(name="호텔", address="서울", body_text="관찰 원문", url=URL, body_read=True)
            with patch.object(server.asyncio, "create_subprocess_exec", return_value=process) as spawn, \
                    patch.object(server, "bounded_stdout", AsyncMock(return_value=json.dumps(output).encode())), \
                    patch.object(server.os, "killpg") as kill, \
                    patch.dict(server.os.environ, {"OPENAI_API_KEY": "unused", "JEV_MCP_TOKEN": "unused"}):
                self.assertEqual(await server.jev_read_lodging(URL), output)
                self.assertEqual(spawn.call_args.args[-1], URL)
                self.assertEqual(spawn.call_args.args[3:5], ("", "45"))
                self.assertNotIn("OPENAI_API_KEY", spawn.call_args.kwargs["env"])
                self.assertNotIn("JEV_MCP_TOKEN", spawn.call_args.kwargs["env"])
            kill.assert_called_once()
            self.assertFalse(server.Path(spawn.call_args.args[2]).exists())
            self.assertFalse(server.lock.locked())

    async def test_busy_timeout_cancel_and_preempt(self):
        with patch.object(server, "preempt_warm", AsyncMock()) as preempt:
            async with server.lock:
                self.assertEqual((await server.jev_read_lodging(URL))["status"], "busy")
            preempt.assert_awaited_once()
        for outcome in ("timeout", "cancel"):
            process = AsyncMock(pid=99999999, returncode=0)
            process.stdout.read.return_value = b""
            started = asyncio.Event()
            async def output(*args):
                started.set()
                if outcome == "timeout":
                    raise TimeoutError
                await asyncio.Event().wait()
            with patch.object(server.asyncio, "create_subprocess_exec", return_value=process), \
                    patch.object(server, "bounded_stdout", side_effect=output), patch.object(server.os, "killpg") as kill:
                task = asyncio.create_task(server.jev_read_lodging(URL))
                await started.wait()
                if outcome == "cancel":
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual((await task)["status"], "timeout")
            kill.assert_called_once()
            process.wait.assert_awaited_once()
            self.assertFalse(server.lock.locked())


if __name__ == "__main__":
    unittest.main()
