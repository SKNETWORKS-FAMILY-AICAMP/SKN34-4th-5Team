"""Read-only new-course inputs, independent of the interpretation model."""
import logging
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from llm.service.chat_runs import Stopped, check_cancelled
from llm.v1.progress import ProgressCancelled, ProgressStorageError

log = logging.getLogger(__name__)


def read(question, code):
    from . import agent, slots
    if code not in agent.STADIUM_KO or agent.PUBLIC_COURSE_LOOKUP.search(question):
        return None
    started = monotonic()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    today = now.date().isoformat()
    result = {"question": question, "code": code, "day": today}
    try:
        check_cancelled()
        requested = agent.requested_date(question, [], today, code)
        check_cancelled()
        schedule, stadium_id = agent.load_schedule(code, requested, now)
        check_cancelled()
        result.update(requested=requested, schedule=schedule, stadium_id=stadium_id)
        game, _, _ = agent.find_game(code, requested or "", today, schedule, now=now, stadium_id=stadium_id)
        # Don't speculate on place lookups when no usable game was found.
        if game and not schedule.get("lookup_failed"):
            check_cancelled()
            result["anchor"] = agent.stadium_anchor(code)
            result["extras"] = {}
            for kind in slots.parse(question, []).get("extras") or []:
                check_cancelled()
                result["extras"][kind] = agent.kakao.nearby(code, kind)
    except (Stopped, ProgressCancelled, ProgressStorageError):
        raise
    except Exception as exc:
        # A speculative read must not turn a previously answerable request into
        # an error. The caller performs its usual lookup for any missing input.
        log.info("course preparation incomplete: %s", type(exc).__name__)
    result["elapsed_ms"] = round((monotonic() - started) * 1000)
    return result


def matches(prepared, question, code, today):
    return bool(prepared and prepared.get("question") == question
                and prepared.get("code") == code and prepared.get("day") == today)
