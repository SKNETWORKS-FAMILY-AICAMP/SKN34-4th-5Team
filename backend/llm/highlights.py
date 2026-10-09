"""KBO 공식 채널 경기 하이라이트 → 장면표(HighlightScene).

collect_highlights 배치가 YouTube Data API 로 영상 목록을 받고, 아직 분석 안 한 영상만 OpenRouter 경유
Gemini(Google AI Studio)에 유튜브 URL 을 넣어 장면을 뽑는다. 질문 시점에는 DB 만 읽는다(search_highlight_scenes).
유튜브 URL 입력은 Google AI Studio provider 만 지원하므로 provider 를 고정한다. Gemini 키는 따로 필요 없다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from django.db import transaction
from django.utils import timezone


UPLOADS = "UUoVz66yWHzVsXAFG8WhJK9g"  # KBO 공식 채널 업로드 목록 (frontend/lib/youtube/kbo-highlight.ts 와 같음)
DEFAULT_MODEL = "google/gemini-3.8-flash"
EVENTS = ("홈런", "안타", "2루타", "3루타", "볼넷", "몸에 맞는 공", "삼진", "아웃", "호수비", "도루", "득점", "실책", "투수 교체", "기타")
# 제목의 구단 표기("삼성라이온즈", "KIA타이거즈") 앞부분 → baseball.Team.team_code
TITLE_TEAM = (("KIA", "KIA"), ("LG", "LG"), ("두산", "DOOSAN"), ("SSG", "SSG"), ("삼성", "SAMSUNG"), ("롯데", "LOTTE"),
              ("한화", "HANWHA"), ("NC", "NC"), ("KT", "KT"), ("키움", "KIWOOM"))
# 설명 첫머리 이름이 타자로 확실한 이벤트만. 삼진·아웃·볼넷은 "고우석이 … 삼진으로 잡아낸다"처럼 투수가 주어일 수 있다
BATTER_EVENTS = ("홈런", "안타", "2루타", "3루타")
LINK_LEAD_SEC = 3  # 플레이 직전부터 보이게 링크 시작을 조금 당긴다


class HighlightError(Exception):
    pass


def env(name):
    """.env 에 'KEY =값'처럼 공백이 섞여 있어도 찾는다."""
    for key, value in os.environ.items():
        if key.strip() == name:
            return value.strip().strip("'\"")
    return ""


def _http_json(url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def is_highlight(title):
    """kbo-highlight.ts isKboLeagueHighlightTitle 과 같은 기준 (퓨처스·크보모먼트 제외)."""
    return bool(re.match(r"^\[[^\]]+\s+vs\s+[^\]]+\]", title, re.I) and "야구 하이라이트" in title
                and re.search(r"\d{4}\s*KBO\s*리그", title, re.I) and re.search(r"KBO\s*X\s*TVING", title, re.I)
                and "퓨처스리그" not in title)


def title_team(text):
    text = text.strip().upper()
    return next((code for prefix, code in TITLE_TEAM if text.startswith(prefix.upper())), None)


def parse_title(title):
    """'[삼성라이온즈 vs KIA타이거즈] 10.6(화) …2026 KBO 리그…' → (원정 코드, 홈 코드, 날짜). 모르면 None."""
    teams = re.match(r"^\[([^\]]+?)\s+vs\s+([^\]]+?)\]", title, re.I)
    when = re.search(r"\]\s*(\d{1,2})\.(\d{1,2})", title)
    year = re.search(r"(\d{4})\s*KBO", title)
    day = None
    if when and year:
        try:
            day = date(int(year.group(1)), int(when.group(1)), int(when.group(2)))
        except ValueError:
            day = None
    away, home = (title_team(teams.group(1)), title_team(teams.group(2))) if teams else (None, None)
    return away, home, day


def seconds(value):
    """'MM:SS' 또는 'H:MM:SS' → 초. 형식이 틀리면 None."""
    parts = str(value or "").strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def iso_duration(value):
    """YouTube contentDetails.duration 'PT14M35S' → 875."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    return None if not match else sum(int(n or 0) * m for n, m in zip(match.groups(), (3600, 60, 1)))


def watch_url(video_id, start_sec=None):
    start = "" if start_sec is None else f"&t={max(0, start_sec - LINK_LEAD_SEC)}s"
    return f"https://www.youtube.com/watch?v={video_id}{start}"


def fetch_videos(since=None, team=None, limit=10, max_pages=10, _http=_http_json):
    """업로드 목록을 최신순으로 훑어 경기 하이라이트만 고른다. since(date) 보다 오래된 영상이 나오면 멈춘다."""
    key = env("YOUTUBE_API_KEY")
    if not key:
        raise HighlightError("YOUTUBE_API_KEY 가 없습니다.")
    found, token = [], None
    for _ in range(max_pages):
        query = {"part": "snippet,contentDetails", "playlistId": UPLOADS, "maxResults": 50, "key": key}
        if token:
            query["pageToken"] = token
        page = _http("https://www.googleapis.com/youtube/v3/playlistItems?" + urllib.parse.urlencode(query))
        for item in page.get("items", []):
            title, video_id = item["snippet"]["title"], item["contentDetails"]["videoId"]
            published = item["contentDetails"].get("videoPublishedAt")
            if since and published and published[:10] < since.isoformat():
                return found
            if not is_highlight(title):
                continue
            away, home, day = parse_title(title)
            if team and team not in (away, home):
                continue
            found.append({"video_id": video_id, "title": title, "published_at": published,
                          "away": away, "home": home, "game_date": day})
            if len(found) >= limit:
                return found
        token = page.get("nextPageToken")
        if not token:
            break
    return found


def fetch_durations(video_ids, _http=_http_json):
    if not video_ids:
        return {}
    query = {"part": "contentDetails", "id": ",".join(video_ids), "key": env("YOUTUBE_API_KEY")}
    page = _http("https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(query))
    return {item["id"]: iso_duration(item["contentDetails"].get("duration")) for item in page.get("items", [])}


def match_game(away, home, day):
    from baseball.models import Game
    if not (away and home and day):
        return None
    games = list(Game.objects.filter(game_date=day, home_team__team_code__in=(away, home),
                                     away_team__team_code__in=(away, home)).order_by("game_time", "id"))
    return games[0] if len(games) == 1 else None  # 더블헤더처럼 여러 경기면 억지로 고르지 않는다


def roster_names(team_codes):
    """두 팀의 저장된 선수 이름. 선수 데이터는 TVING 크롤러가 채운다(tving.service 는 화면 요청에서 DB 만 읽는다).
    이름이 적어도 분석은 진행하고, 모델이 자막에서 읽은 이름은 batter_name 에 그대로 남는다."""
    from baseball.models import Player
    return {code: sorted(set(Player.objects.filter(team__team_code=code).values_list("name", flat=True)))
            for code in filter(None, team_codes)}


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["scenes"], "properties": {"scenes": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["start", "end", "inning", "half", "batting_team", "batter", "pitcher", "event", "rbi", "description"],
        "properties": {
            "start": {"type": "string", "description": "영상 시작 기준 MM:SS"},
            "end": {"type": "string", "description": "MM:SS"},
            "inning": {"type": ["integer", "null"]}, "half": {"type": ["string", "null"], "enum": ["초", "말", None]},
            "batting_team": {"type": ["string", "null"]}, "batter": {"type": ["string", "null"]},
            "pitcher": {"type": ["string", "null"]}, "event": {"type": "string", "enum": list(EVENTS)},
            "rbi": {"type": ["integer", "null"]}, "description": {"type": "string"}}}}}}


def prompt(title, rosters):
    roster = "\n".join(f"- {code}: {', '.join(names) or '(이름 없음)'}" for code, names in rosters.items()) or "(로스터 없음)"
    return f"""이 영상은 KBO 공식 경기 하이라이트다: {title}
영상에 나오는 플레이 장면을 시간순으로 빠짐없이 나열해라.
- start/end: 영상 시작 기준 MM:SS (그 플레이가 화면에 처음 보이는 시각과 끝나는 시각)
- 선수 이름은 화면 자막과 중계 멘트로 확인한다. 아래 로스터에 있으면 그 표기로 쓰고, 확실하지 않으면 null.
- batting_team 은 공격 중인 팀 코드(아래 로스터의 코드 중 하나), 모르면 null.
- event 는 정해진 목록 중 하나. rbi 는 그 플레이로 들어온 타점 수, 모르면 null.
- inning/half 는 화면 스코어보드나 멘트로 확인, 모르면 null.
- pitcher 는 화면 스코어보드·하단 선수 그래픽의 투수 이름(투구 수 옆, "P"·"투수" 표시)과 중계 멘트로 확인한다.
  타석 장면마다 스코어보드를 꼭 확인해 채우고, 같은 이닝에서 '투수 교체' 전까지는 앞서 확인한 투수와 같다.
  투수 교체 장면의 pitcher 에는 새로 올라온 투수를 쓴다.
- 그 밖에는 추측하지 말고 보이는/들리는 것만 쓴다. description 은 한 문장 한국어.
로스터:
{roster}"""


def analyze(video_id, title, rosters, model=DEFAULT_MODEL, _http=_http_json):
    """OpenRouter → Gemini(Google AI Studio)로 장면표를 받는다. 반환 (scenes, cost, provider)."""
    key = env("OPENROUTER_API_KEY")
    if not key:
        raise HighlightError("OPENROUTER_API_KEY 가 없습니다.")
    body = {"model": model, "usage": {"include": True},
            "provider": {"only": ["google-ai-studio"], "allow_fallbacks": False},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt(title, rosters)},
                {"type": "video_url", "video_url": {"url": watch_url(video_id)}}]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "scenes", "strict": True, "schema": SCHEMA}}}
    headers = {"Authorization": f"Bearer {key}", "X-Title": "kbo-highlight-scenes"}
    try:
        response = _http("https://openrouter.ai/api/v1/chat/completions", body, headers, timeout=300)
    except urllib.error.HTTPError as exc:
        raise HighlightError(f"OpenRouter {exc.code}: {exc.read()[:300].decode(errors='replace')}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:  # 끊김·시간 초과·JSON 아닌 응답: 이 영상만 실패로
        raise HighlightError(f"OpenRouter 호출 실패: {type(exc).__name__}: {exc}") from None
    try:
        text = (response["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise HighlightError(f"OpenRouter 응답 형식 오류: {str(response)[:200]}") from None
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    try:
        scenes = json.loads(text)["scenes"]
    except (ValueError, KeyError, TypeError):
        raise HighlightError(f"장면 JSON 을 읽지 못했습니다: {text[:200]}") from None
    return scenes, float((response.get("usage") or {}).get("cost") or 0), response.get("provider")


def clean_scenes(raw, duration_sec, team_codes):
    """모델 출력 검증: 시각 형식·영상 길이 안·이벤트 목록·팀 코드. 못 맞춘 값은 버리거나 비운다."""
    from baseball.models import Player
    scenes = []
    for item in raw:
        start, end = seconds(item.get("start")), seconds(item.get("end"))
        if start is None or (duration_sec and start > duration_sec) or item.get("event") not in EVENTS:
            continue
        team = item.get("batting_team")
        team = team if team in team_codes else title_team(team or "")
        team = team if team in team_codes else None
        name = (item.get("batter") or "").strip()[:80]
        description = (item.get("description") or "").strip()
        if not name and item.get("event") in BATTER_EVENTS:  # "나승엽이 우측 폴대를 맞히는 …" → 나승엽
            lead = re.match(r"^(?:대타\s+)?([가-힣]{2,4}?)(?:이|가)\s", description)
            name = lead.group(1) if lead else ""
        players = Player.objects.filter(name=name, team__team_code__in=[team] if team else team_codes) if name else Player.objects.none()
        scenes.append({
            "start_sec": start, "end_sec": end if end is not None and end >= start else None,
            "inning": item.get("inning") if isinstance(item.get("inning"), int) and 1 <= item["inning"] <= 20 else None,
            "half": item.get("half") if item.get("half") in ("초", "말") else "",
            "team_code": team, "batter": players.first() if players.count() == 1 else None, "batter_name": name,
            "pitcher_name": (item.get("pitcher") or "").strip()[:80], "event": item["event"],
            "rbi": item.get("rbi") if isinstance(item.get("rbi"), int) and 0 <= item["rbi"] <= 4 else None,
            "description": description,
        })
    return fill_pitchers(scenes)


def fill_pitchers(scenes):
    """같은 이닝·초말 안에서는 '투수 교체' 전까지 투수가 같다: 비어 있는 투수 칸을 앞 장면의 투수로 채운다.
    투수 교체 장면은 새 투수(비어 있으면 설명 첫머리 이름)로 바꾼다. 이닝을 넘겨 이어 붙이지는 않는다."""
    current = {}
    for scene in sorted(scenes, key=lambda s: s["start_sec"]):
        key = (scene["inning"], scene["half"])
        if scene["inning"] is None or not scene["half"]:
            continue
        if scene["event"] == "투수 교체":
            lead = re.match(r"^([가-힣]{2,4}?)(?:이|가)\s", scene["description"])
            scene["pitcher_name"] = scene["pitcher_name"] or (lead.group(1) if lead else "")
            current[key] = scene["pitcher_name"]
        elif scene["pitcher_name"]:
            current[key] = scene["pitcher_name"]
        elif current.get(key):
            scene["pitcher_name"] = current[key]
    return scenes


def refill_pitchers(report=print):
    """저장된 장면에 fill_pitchers 를 다시 적용한다(영상 재분석 없음, 비용 없음)."""
    from llm.models import HighlightScene, HighlightVideo
    changed = 0
    for video in HighlightVideo.objects.filter(status=HighlightVideo.ANALYZED):
        rows = list(video.scenes.all())
        scenes = [{"start_sec": r.start_sec, "inning": r.inning, "half": r.half, "event": r.event,
                   "pitcher_name": r.pitcher_name, "description": r.description, "row": r} for r in rows]
        for scene in fill_pitchers(scenes):
            if scene["pitcher_name"] != scene["row"].pitcher_name:
                scene["row"].pitcher_name = scene["pitcher_name"]
                changed += 1
        HighlightScene.objects.bulk_update(rows, ["pitcher_name"])
    report(f"투수 칸 채움: {changed}개 장면")
    return changed


def save_video(info, duration_sec):
    from baseball.models import Team
    from llm.models import HighlightVideo
    teams = {t.team_code: t for t in Team.objects.filter(team_code__in=[c for c in (info["home"], info["away"]) if c])}
    video, _ = HighlightVideo.objects.update_or_create(video_id=info["video_id"], defaults={
        "title": info["title"], "published_at": info["published_at"], "duration_sec": duration_sec,
        "game_date": info["game_date"], "game": match_game(info["away"], info["home"], info["game_date"]),
        "home_team": teams.get(info["home"]), "away_team": teams.get(info["away"])})
    return video


@transaction.atomic
def replace_scenes(video, scenes, model, cost):
    from baseball.models import Team
    from llm.models import HighlightScene
    teams = {t.team_code: t for t in Team.objects.filter(team_code__in={s["team_code"] for s in scenes if s["team_code"]})}
    video.scenes.all().delete()
    HighlightScene.objects.bulk_create(HighlightScene(
        video=video, batting_team=teams.get(s.pop("team_code")), **s) for s in scenes)
    video.status, video.error, video.model, video.cost, video.analyzed_at = video.ANALYZED, "", model, cost, timezone.now()
    video.save(update_fields=["status", "error", "model", "cost", "analyzed_at", "updated_at"])


def collect(since=None, team=None, limit=5, budget=1.0, model=DEFAULT_MODEL, video_ids=(), reanalyze=False,
            dry_run=False, report=print, _http=_http_json):
    """목록 수집 → (dry_run 이 아니면) 새 영상만 분석·저장. 누적 비용이 budget(USD)에 닿으면 멈춘다."""
    from llm.models import HighlightVideo
    if video_ids:
        infos = [{"video_id": v, "title": "", "published_at": None, "away": None, "home": None, "game_date": None} for v in video_ids]
        meta = _http("https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(
            {"part": "snippet", "id": ",".join(video_ids), "key": env("YOUTUBE_API_KEY")}))
        titles = {item["id"]: item["snippet"] for item in meta.get("items", [])}
        for info in infos:
            snippet = titles.get(info["video_id"], {})
            info["title"], info["published_at"] = snippet.get("title", ""), snippet.get("publishedAt")
            info["away"], info["home"], info["game_date"] = parse_title(info["title"])
    else:
        infos = fetch_videos(since, team, limit, _http=_http)
    durations = fetch_durations([i["video_id"] for i in infos], _http=_http)
    spent, summary = 0.0, {"found": len(infos), "analyzed": 0, "skipped": 0, "failed": 0, "cost": 0.0}
    for info in infos:
        done = HighlightVideo.objects.filter(video_id=info["video_id"], status=HighlightVideo.ANALYZED).exists()
        label = f"{info['game_date']} {info['away']} vs {info['home']} {info['video_id']}"
        if done and not reanalyze:
            summary["skipped"] += 1
            report(f"건너뜀(이미 분석): {label}")
            continue
        if dry_run:
            report(f"분석 예정: {label} | {info['title']}")
            continue
        if spent >= budget:
            report(f"예산 ${budget} 도달로 중단")
            break
        video = save_video(info, durations.get(info["video_id"]))
        started = time.monotonic()
        try:
            codes = [c for c in (info["away"], info["home"]) if c]
            raw, cost, provider = analyze(info["video_id"], info["title"], roster_names(codes), model, _http=_http)
            scenes = clean_scenes(raw, video.duration_sec, codes)
            replace_scenes(video, scenes, model, cost)
        except HighlightError as exc:
            video.status, video.error = video.FAILED, str(exc)[:1000]
            video.save(update_fields=["status", "error", "updated_at"])
            summary["failed"] += 1
            report(f"실패: {label} | {exc}")
            continue
        spent += cost
        summary["analyzed"] += 1
        homers = sum(1 for s in scenes if s["event"] == "홈런")
        report(f"분석: {label} | 장면 {len(scenes)}개(홈런 {homers}) | ${cost:.4f} | {time.monotonic() - started:.0f}초 | {provider}")
    summary["cost"] = round(spent, 4)
    return summary
