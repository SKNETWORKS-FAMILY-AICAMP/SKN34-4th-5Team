"""Known NOL URLs share the existing MCP transport, without search or local launch."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import Tool

from llm.tests.test_lodging_chrome import page
from llm.tests.test_lodging_conditions import URL, PLACE
from llm.v2.agent import browser_research as browser
from llm.v2.nearby import lodging
from llm.service.chat_runs import Stopped
from travel import chrome_lodging as chrome


class LodgingRoutingTests(SimpleTestCase):
    def test_attachment_body_direct_selection_and_generic_envelope(self):
        payload = {**page(), "collected_at": "2026-10-13T01:55:49.185Z"}
        invoke = AsyncMock(return_value=[{"type": "text", "text": json.dumps(payload)}])
        with patch.object(browser, "discover_tools", AsyncMock(return_value=[SimpleNamespace(ainvoke=invoke)])) as discover:
            result = browser.web_body(URL)
        discover.assert_awaited_once_with({"jev_read_lodging"})
        invoke.assert_awaited_once_with({"url": URL})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["body"], payload["body_text"])
        self.assertEqual(result["requested_url"], URL)
        self.assertEqual(result["source_kind"], "rendered_dom_snapshot")

    def test_direct_failures_and_wrong_property_are_not_original_body(self):
        for payload in ({"status": "blocked"}, {"status": "partial"},
                        {**page(), "url": "https://nol.yanolja.com/stay/domestic/999"},
                        {**page(), "body_read": False}):
            with self.subTest(payload=payload), patch.object(chrome, "_remote", AsyncMock(return_value=payload)):
                result = browser.web_body(URL)
            self.assertNotEqual(result["status"], "ok")
            self.assertNotIn("body", result)
        with patch.object(chrome, "_remote", AsyncMock(side_effect=Stopped)), self.assertRaises(Stopped):
            browser.web_body(URL)
        self.assertFalse(browser._admission.locked())

    def test_generic_and_complex_urls_keep_existing_tools(self):
        tools = [SimpleNamespace(name=n, ainvoke=AsyncMock(return_value={"status": "blocked"}))
                 for n in ("jev_read_body", "jev_browse")]
        with patch.object(browser, "discover_tools", AsyncMock(return_value=tools)):
            asyncio.run(browser.browse("https://example.com", "", body=True))
            asyncio.run(browser.browse(URL, "inspect page"))
        tools[0].ainvoke.assert_awaited_once_with({"url": "https://example.com"})
        tools[1].ainvoke.assert_awaited_once_with({"url": URL, "goal": "inspect page"})
        self.assertFalse(browser.lodging_url(URL + "#fragment"))
        self.assertFalse(browser.lodging_url("https://nol.yanolja.com:443/stay/domestic/123"))

    def test_web_exposed_body_schema_automatically_routes_direct_mcp(self):
        tool = convert_mcp_tool_to_langchain_tool(None, Tool(name="jev_read_body", description="body",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
            connection={"transport": "streamable_http", "url": "http://offline.test/mcp"})
        with patch.object(browser, "discover_tools", AsyncMock(return_value=[tool])):
            adapted = browser.direct_tools()[0]
        with patch.object(chrome, "_remote", AsyncMock(return_value={**page(), "collected_at": "2026-10-13T01:55:49Z"})) as remote:
            result = adapted.invoke({"name": "jev_read_body", "id": "direct", "type": "tool_call", "args": {"url": URL}})
        self.assertEqual(result.status, "success")
        remote.assert_awaited_once_with("", 45, url=URL)
        self.assertIn(page()["body_text"], json.loads(result.content)["body"])

    def test_direct_body_whole_call_budget_retains_remote_arguments(self):
        original_wait_for = asyncio.wait_for
        with patch.object(chrome, "_remote", AsyncMock(return_value={**page(), "collected_at": "2026-10-13T01:55:49Z"})) as remote, patch.object(browser.asyncio, "wait_for", wraps=original_wait_for) as wait_for:
            self.assertEqual(browser.web_body(URL)["status"], "ok")
        remote.assert_awaited_once_with("", 45, url=URL)
        self.assertEqual(wait_for.await_count, 1)
        self.assertEqual(wait_for.call_args.kwargs, {"timeout": 48})

    def test_stalled_direct_body_is_cancelled_and_admission_reusable(self):
        original_wait_for = asyncio.wait_for
        tool = convert_mcp_tool_to_langchain_tool(None, Tool(name="jev_read_body", description="body",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
            connection={"transport": "streamable_http", "url": "http://offline.test/mcp"})
        with patch.object(browser, "discover_tools", AsyncMock(return_value=[tool])):
            adapted = browser.direct_tools()[0]
        cancelled = []

        async def stalled(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        async def short_wait_for(awaitable, *, timeout):
            self.assertEqual(timeout, 48)
            return await original_wait_for(awaitable, timeout=0.01)

        for direct in (False, True):
            with self.subTest(direct=direct), patch.object(chrome, "_remote", side_effect=stalled) as remote, patch.object(browser.asyncio, "wait_for", side_effect=short_wait_for):
                if direct:
                    result = adapted.invoke({"name": "jev_read_body", "id": "timeout", "type": "tool_call", "args": {"url": URL}})
                    self.assertEqual(result.status, "error")
                    self.assertEqual(json.loads(result.content)["status"], "timeout")
                else:
                    self.assertEqual(browser.web_body(URL), {"status": "timeout", "source_url": URL})
                remote.assert_awaited_once_with("", 45, url=URL)
            self.assertEqual(len(cancelled), 1 + int(direct))
            self.assertFalse(browser._admission.locked())
            with patch.object(chrome, "_remote", AsyncMock(return_value={**page(), "collected_at": "2026-10-13T01:55:49Z"})):
                self.assertEqual(browser.web_body(URL)["status"], "ok")

    def test_actual_missing_lodging_tool_returns_error_and_releases_admission(self):
        tool = convert_mcp_tool_to_langchain_tool(None, Tool(name="jev_read_body", description="body",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
            connection={"transport": "streamable_http", "url": "http://offline.test/mcp"})
        tools = [tool, tool.model_copy(update={"name": "jev_browse"})]
        with patch.dict("os.environ", {"JEV_MCP_TOKEN": "offline-test"}), patch(
                "langchain_mcp_adapters.client.MultiServerMCPClient.get_tools", AsyncMock(return_value=tools)) as discover:
            adapted = browser.direct_tools()[0]
            self.assertEqual(browser.web_body(URL), {"status": "error", "source_url": URL})
            self.assertFalse(browser._admission.locked())
            result = adapted.invoke({"name": "jev_read_body", "id": "missing", "type": "tool_call", "args": {"url": URL}})
            self.assertEqual(result.status, "error")
            self.assertEqual(json.loads(result.content)["status"], "error")
            self.assertFalse(browser._admission.locked())
            self.assertEqual(discover.await_count, 3)
        with patch.object(chrome, "_remote", AsyncMock(return_value={**page(), "collected_at": "2026-10-13T01:55:49Z"})):
            self.assertEqual(browser.web_body(URL)["status"], "ok")

    def test_discovery_unknown_runtime_and_stop_propagate_from_both_boundaries(self):
        tool = convert_mcp_tool_to_langchain_tool(None, Tool(name="jev_read_body", description="body",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
            connection={"transport": "streamable_http", "url": "http://offline.test/mcp"})
        with patch.object(browser, "discover_tools", AsyncMock(return_value=[tool])):
            adapted = browser.direct_tools()[0]
        for error in (RuntimeError("unrelated defect"), Stopped(), asyncio.CancelledError()):
            for direct in (False, True):
                with self.subTest(error=type(error), direct=direct), patch.dict("os.environ", {"JEV_MCP_TOKEN": "offline-test"}), patch(
                        "langchain_mcp_adapters.client.MultiServerMCPClient.get_tools", AsyncMock(side_effect=error)), self.assertRaises(type(error)):
                    if direct:
                        adapted.invoke({"name": "jev_read_body", "id": "unknown", "type": "tool_call", "args": {"url": URL}})
                    else:
                        browser.web_body(URL)
                self.assertFalse(browser._admission.locked())
        self.assertIsNone(browser.transport_status(RuntimeError("JEV_MCP_TOKEN is required")))
        self.assertIsNone(browser.transport_status(ExceptionGroup("mixed", [RuntimeError("Required JEV MCP tools unavailable"), RuntimeError("unknown")])))

    def test_trailing_slash_is_trusted_before_canonical_direct_routing(self):
        invoke = AsyncMock(return_value={**page(), "collected_at": "2026-10-13T01:55:49Z"})
        with patch.object(browser, "discover_tools", AsyncMock(return_value=[SimpleNamespace(ainvoke=invoke)])) as discover:
            self.assertEqual(browser.web_body(URL + "/")["status"], "ok")
        discover.assert_awaited_once_with({"jev_read_lodging"})
        invoke.assert_awaited_once_with({"url": URL})
        for url in (URL + "/?other=1", URL + "?verticalCategory=PRODUCT_CATEGORY_KOREA_ACCOMMODATION&other=1",
                    URL.replace("nol.yanolja.com", "user@nol.yanolja.com"), URL.replace("nol.yanolja.com", "nol.yanolja.com:443")):
            self.assertFalse(browser.lodging_url(url))

    def test_collection_timestamp_is_required_and_never_synthesized(self):
        stamp = "2026-10-13T01:55:49.185Z"
        for value in (None, "bad", "2026-10-13T01:55:49", stamp):
            payload = {**page(), **({"collected_at": value} if value is not None else {})}
            with self.subTest(value=value), patch.object(chrome, "_remote", AsyncMock(return_value=payload)):
                result = browser.web_body(URL)
            self.assertEqual(result["status"], "ok" if value == stamp else "error")
            if value == stamp:
                self.assertEqual(result["collected_at"], stamp)
            else:
                self.assertNotIn("body", result)

    def test_course_candidate_retains_known_url(self):
        with patch.object(lodging, "requirements", return_value=[]), patch.object(lodging, "collect_chrome", return_value={"status": "blocked"}) as collect:
            lodging.verify([{**PLACE, "sourceUrl": URL}], "숙소")
        self.assertEqual(collect.call_args.args[0]["sourceUrl"], URL)
