"""Branch-matched bar ratings, independent of optional interior approval.

Reuse the existing public profile reader. Missing ratings never become a pass;
fresh reads are bounded and do not invoke a search or image-analysis model.
"""
import logging
import math
import re
import time
from datetime import date
from itertools import islice

from llm.service.chat_runs import Stopped, check_cancelled
from llm.v1.progress import ProgressCancelled, ProgressStorageError
from . import place_quality as quality
from .timeline import BAR_WORDS

log = logging.getLogger(__name__)
MAX_CANDIDATES = 6
READ_SECONDS = 12


def is_bar(place, category=None):
    detail = str(place.get("detail") or place.get("category_name") or place.get("category_detail") or "")
    return category == "BAR" or place.get("category") == "BAR" or any(word in detail for word in (*BAR_WORDS, "주점"))


def valid(row):
    try:
        age = (date.today() - date.fromisoformat(row["checkedAt"])).days
        score, count = row["rating"], row["ratingCount"]
        return (0 <= age <= 180 and type(score) in (int, float) and math.isfinite(score) and 0 < score <= 5
                and type(count) is int and count >= 1
                and bool(re.fullmatch(r"https://www\.diningcode\.com/profile\.php\?rid=[A-Za-z0-9]{6,40}", row["reviewUrl"])))
    except (KeyError, TypeError, ValueError):
        return False


def cache_key(place):
    return "bar-rating-v1:" + quality.cache_key(place)


def resolve(place):
    if not quality._id(place):
        return None
    rows = [quality.verdict_cache().get(cache_key(place), {}).get("row"),
            quality.verdict_cache().get(quality.cache_key(place), {}).get("row"),
            quality.catalogue().get(quality._id(place))]
    for row in sorted((r for r in rows if isinstance(r, dict)), key=lambda r: r.get("checkedAt", ""), reverse=True):
        if valid(row) and quality.same_branch(place, row):
            return row
    return None


def save(item, proof):
    row = {**item, "rating": proof["rating"], "ratingCount": proof["ratingCount"],
           "ratingSource": "다이닝코드", "reviewUrl": proof["url"], "checkedAt": date.today().isoformat()}
    if valid(row):
        quality.verdict_cache().set(cache_key(item), {"row": row}, 7 * 86400)


def investigate(places, category, *, center=None, radius=2500):
    state = quality._RESEARCH.get()
    if state is None or quality.probing() or not quality._FRESH.get():
        return
    from . import availability
    bars = [p for p in places if is_bar(p, category) and quality.candidate_allowed(p)
            and not availability.check(p, availability.visit_date())]
    if not bars:
        return
    bars.sort(key=lambda p: quality.distance_to(p, center))
    # Reuse only within the closest local band. A distant cached bar does not
    # prevent investigating closer branches at this visit's actual location.
    nearest = min((quality.distance_to(p, center) for p in bars), default=float("inf"))
    limit = min(radius, quality.LOCAL_REUSE_M, nearest + quality.LOCAL_DISTANCE_SLACK_M)
    if center and any(quality.distance_to(p, center) <= limit and resolve(p)
                      and quality.filter_place(p, category) is not None for p in bars):
        return
    tried = state.setdefault("rating_tried", set())
    queue = []
    for p in bars:
        key = cache_key(p)
        item = quality.canonical(p)
        if (item and key not in tried and not resolve(p)
                and not quality.resolve(p, include_rejected=True)
                and quality.verdict_cache().get(key) is None):
            queue.append((item, key))
    queue = queue[:max(0, MAX_CANDIDATES - len(tried))]
    if not queue:
        return
    from travel.public_page_reader import PublicReader
    from . import parallel, quality_research
    deadline = state.setdefault("rating_deadline", time.monotonic() + READ_SECONDS)
    if time.monotonic() >= deadline:
        return
    reader = quality_research.ProfileReader(PublicReader(deadline=deadline, max_requests=24, gap=.25))
    def read(item):
        try:
            for url in islice(quality_research.discover(item, reader), 6):
                check_cancelled()
                if proof := quality_research.profile(item, url, reader, min_ratings=1):
                    return proof
        except (Stopped, ProgressCancelled, ProgressStorageError):
            raise
        except Exception as exc:
            log.warning("bar rating read failed: %s", type(exc).__name__)
        return None
    try:
        while queue and time.monotonic() < deadline:
            check_cancelled()
            batch, queue = queue[:parallel.WORKERS], queue[parallel.WORKERS:]
            tried.update(key for _, key in batch)
            proofs = parallel.reads(lambda item=item: read(item) for item, _ in batch)
            for (item, key), proof in zip(batch, proofs):
                if proof:
                    save(item, proof)
                else:
                    # Cool down empty/blocked reads without claiming zero stars.
                    quality.verdict_cache().set(key, {"row": None}, 3600)
            if any(proofs):
                break
    finally:
        reader.close()
