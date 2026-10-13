"""JEV MCP-owned Chrome collection. Never calls a search or browser-agent model."""
import asyncio
import hashlib
import json
import pickle
import time
from threading import Lock

from llm.service.chat_runs import Stopped, check_cancelled

_admission = Lock()
MAX_OUTPUT = 2 * 1024 * 1024
MAX_SECONDS = 45
CACHE_SECONDS = 6 * 60 * 60


def _page_cache():
    from django.core.cache import caches, InvalidCacheBackendError
    try:
        return caches["lodging_pages"]
    except InvalidCacheBackendError:
        return caches["default"]


def _validated(candidate, page):
    from llm.v2.nearby.lodging import same_property, source_url, _compact
    from llm.v2.course.grounding import address_in_body
    if not isinstance(page, dict) or page.get("status") != "read":
        return None
    url, body = source_url(page.get("url")), page.get("body_text")
    if (not url or page.get("source_kind") != "rendered_dom_snapshot" or page.get("body_read") is not True
            or not isinstance(body, str) or not body.strip() or len(body) > 300_000
            or not isinstance(page.get("title"), str) or len(page["title"]) > 4096
            or not isinstance(page.get("name"), str) or not isinstance(page.get("address"), str)
            or not same_property(candidate, page) or not address_in_body(page.get("address", ""), body)
            or not _compact(page.get("name")) or _compact(page["name"]) not in _compact(body)):
        return None
    known_url = candidate.get("sourceUrl") or candidate.get("url")
    if source_url(known_url) and source_url(known_url) != url:
        return None
    return {**page, "url": url}


async def _remote(name, seconds, *, background=False, url=None, region=""):
    from llm.v2.agent.browser_research import cancellable_call, discover_tools
    from llm.v2.agent.browser_research import lodging_url
    direct = lodging_url(url) if not background else False
    if direct:
        from llm.v2.nearby.lodging import source_url
        url = source_url(url)
    tools = await cancellable_call(discover_tools({"jev_read_lodging" if direct else "chrome_lodging"}))
    arguments = {"url": url} if direct else {"name": name, "timeout_seconds": seconds,
                                           **({"background": True} if background else {}),
                                           **({"region": region} if region else {})}
    value = await cancellable_call(tools[0].ainvoke(arguments))
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT:
        return {"status": "overflow"}
    if isinstance(value, list):
        if len(value) != 1 or not isinstance(value[0], dict) or value[0].get("type") != "text":
            return {"status": "error"}
        value = value[0]["text"]
    return json.loads(value) if isinstance(value, str) else value


def _cache_key(candidate):
    identity = [str(candidate.get(k) or "") for k in ("id", "name", "address")]
    return "lodging:page:v1:" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def cached(candidate):
    """Revalidate an unexpired snapshot without extending its lifetime."""
    try:
        entry = _page_cache().get(_cache_key(candidate))
        remaining = min(CACHE_SECONDS, entry["expires_at"] - time.time()) if isinstance(entry, dict) else 0
        if remaining > 0 and (page := _validated(candidate, entry.get("page"))):
            check_cancelled()
            return {**page, "cache_hit": True, "cache_remaining_seconds": int(remaining)}
    except (OSError, ValueError, TypeError, KeyError, EOFError, pickle.UnpicklingError):
        pass  # Cache failure must not prevent a fresh, validated collection.
    return None


def remember(candidate):
    """Approximate demand for public properties, never user text/identity.

    Concurrent counters may lose an increment; they only order background work.
    They never affect recommendation order or evidence validity.
    """
    try:
        key = _cache_key(candidate)
        recent = _page_cache().get("lodging:warm:recent", {})
        recent = recent if isinstance(recent, dict) else {}
        old = recent.get(key, {})
        recent[key] = {"candidate": {k: str(candidate.get(k) or "")[:255] for k in ("id", "name", "address")},
                       "hits": min(10000, int(old.get("hits", 0)) + 1), "last_used": time.time()}
        recent = dict(sorted(recent.items(), key=lambda item: item[1]["last_used"], reverse=True)[:128])
        _page_cache().set("lodging:warm:recent", recent, 7 * 24 * 3600)
    except (OSError, ValueError, TypeError, KeyError, EOFError, pickle.UnpicklingError):
        pass


def collect(candidate, *, deadline, refresh_before=0, background=False):
    """Return verified source data. Refreshes never replace good data with failure."""
    check_cancelled()
    if deadline <= time.monotonic():
        return {"status": "timeout"}
    if page := cached(candidate):
        if page["cache_remaining_seconds"] > max(0, refresh_before):
            if not background:
                remember(candidate)
            return page
    key = _cache_key(candidate)
    if not _admission.acquire(blocking=False):
        return {"status": "busy"}
    try:
        deadline = min(deadline, time.monotonic() + MAX_SECONDS)
        seconds = deadline - time.monotonic()
        if seconds < 1:
            return {"status": "timeout"}
        try:
            page = asyncio.run(asyncio.wait_for(_remote(candidate["name"], seconds,
                background=background, url=candidate.get("sourceUrl") or candidate.get("url"),
                region=" ".join(str(candidate.get("address") or "").split()[:2])), timeout=seconds + 3))
        except Stopped:
            raise
        except TimeoutError:
            return {"status": "timeout"}
        except (OSError, ValueError, RuntimeError):
            return {"status": "error"}
        except Exception as error:
            from llm.v2.agent.browser_research import transport_status
            if status := transport_status(error):
                return {"status": status}
            raise
        check_cancelled()
        if not isinstance(page, dict):
            return {"status": "error"}
        if page.get("status") != "read":
            status = page.get("status")
            stage = page.get("stage")
            return {"status": status if status in {"busy", "timeout", "blocked", "partial", "not_found", "ambiguous", "unavailable", "overflow"} else "error",
                    **({"stage": stage} if stage in {"startup", "home_navigation", "search_input", "search_navigation",
                                                    "search_results", "detail_startup", "detail_navigation", "detail_content"} else {})}
        page = _validated(candidate, page)
        if page is None:
            return {"status": "unverified"}
        check_cancelled()
        try:
            _page_cache().set(key, {"expires_at": time.time() + CACHE_SECONDS, "page": page}, CACHE_SECONDS)
        except (OSError, ValueError, TypeError):
            pass
        if not background:
            remember(candidate)
        return {**page, "cache_hit": False, "cache_remaining_seconds": CACHE_SECONDS}
    finally:
        _admission.release()
