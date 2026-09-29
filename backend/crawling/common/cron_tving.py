import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from django.test import override_settings

# 수집 기준 시간대
KST = ZoneInfo("Asia/Seoul")

# TVING 코드 → 내부 KBO 팀 코드 변환
TEAM_MAP = {
    "SS": "SAMSUNG",
    "KT": "KT",
    "LG": "LG",
    "HT": "KIA",
    "OB": "DOOSAN",
    "NC": "NC",
    "HH": "HANWHA",
    "LT": "LOTTE",
    "SK": "SSG",
    "WO": "KIWOOM",
}

STADIUM_MAP = {
    "잠실": "JAMSIL",
    "고척": "GOCHEOK",
    "문학": "MUNHAK",
    "수원": "SUWON",
    "대전": "DAEJEON",
    "대구": "DAEGU",
    "광주": "GWANGJU",
    "사직": "SAJIK",
    "창원": "CHANGWON",
}

STADIUM_NAMES = {
    "JAMSIL": "잠실야구장", "GOCHEOK": "고척스카이돔", "MUNHAK": "인천 SSG 랜더스필드",
    "SUWON": "수원 KT 위즈 파크", "DAEJEON": "대전 한화생명 볼파크", "DAEGU": "대구 삼성 라이온즈 파크",
    "GWANGJU": "광주-KIA 챔피언스 필드", "SAJIK": "사직야구장", "CHANGWON": "창원 NC 파크",
}

TEAM_HOME = {
    "LG": "JAMSIL",
    "DOOSAN": "JAMSIL",
    "KIWOOM": "GOCHEOK",
    "SSG": "MUNHAK",
    "KT": "SUWON",
    "HANWHA": "DAEJEON",
    "SAMSUNG": "DAEGU",
    "KIA": "GWANGJU",
    "LOTTE": "SAJIK",
    "NC": "CHANGWON",
}

TEAM_KO = {
    "LG": "LG 트윈스",
    "DOOSAN": "두산 베어스",
    "KIWOOM": "키움 히어로즈",
    "SSG": "SSG 랜더스",
    "KT": "KT 위즈",
    "HANWHA": "한화 이글스",
    "SAMSUNG": "삼성 라이온즈",
    "KIA": "KIA 타이거즈",
    "LOTTE": "롯데 자이언츠",
    "NC": "NC 다이노스",
}

def _setup():
    """Django 프로젝트를 초기화해 독립 실행 스크립트에서도 ORM을 사용할 수 있게 한다."""
    # 현재 스크립트 기준 프로젝트 루트를 계산한다.
    project_root = Path(__file__).resolve().parents[2]
    # 프로젝트 루트가 sys.path에 없으면 추가한다.
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    # Django 설정 모듈 지정
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Django 초기화
    import django
    django.setup()

def _vector_upsert(items):
    """수집한 문서를 임베딩한 뒤 pgvector에 신규 저장하거나 기존 데이터를 갱신한다."""
    from django.db import transaction
    from langchain_openai import OpenAIEmbeddings
    from llm.models import Document, DocumentChunk
    pending, skipped = [], 0
    for item in items:
        digest = hashlib.sha256(item["content"].encode()).hexdigest()
        meta = item.get("metadata", {})
        old = DocumentChunk.objects.filter(metadata__doc_id=item["doc_id"]).first()
        if old is None and item.get("metadata", {}).get("category") == "SCHEDULE":
            meta = item["metadata"]
            old = DocumentChunk.objects.filter(
                metadata__source_file="kbo_schedule_full.csv",
                metadata__game_date=meta.get("game_date"),
                metadata__game_time=meta.get("game_time"),
                metadata__away_team_code=meta.get("away_team_code"),
                metadata__home_team_code=meta.get("home_team_code"),
            ).first()
        if old is None and item.get("metadata", {}).get("category") == "STANDING":
            old = DocumentChunk.objects.filter(
                metadata__source_file="kbo_standing.csv",
                metadata__team_code=meta.get("team_code"),
            ).first()
        if old is None and item.get("metadata", {}).get("category") == "TICKET_POLICY":
            old = DocumentChunk.objects.filter(content=item["content"]).first()
        if old and old.metadata.get("content_hash") == digest:
            metadata = {**old.metadata, **item.get("metadata", {}), "doc_id": item["doc_id"], "content_hash": digest}
            if old.metadata != metadata:
                old.metadata = metadata
                old.save(update_fields=("metadata",))
            skipped += 1
        else:
            pending.append((item, digest, old))
    if not pending:
        return {"new": 0, "updated": 0, "skipped": skipped}
    vectors = OpenAIEmbeddings(model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")).embed_documents([x[0]["content"] for x in pending])
    with transaction.atomic():
        new = updated = 0
        for (item, digest, old), vector in zip(pending, vectors):
            metadata = {**old.metadata, **item.get("metadata", {}), "doc_id": item["doc_id"], "content_hash": digest} if old else {**item.get("metadata", {}), "doc_id": item["doc_id"], "content_hash": digest}
            source = metadata.get("source_file", "tving-crawler")
            document, _ = Document.objects.get_or_create(source=source, defaults={"title": source})
            if old:
                old.document = document
                old.content, old.metadata, old.embedding = item["content"], metadata, vector
                old.save(update_fields=("document", "content", "metadata", "embedding"))
                updated += 1
            else:
                DocumentChunk.objects.create(document=document, content=item["content"], chunk_index=0, metadata=metadata, embedding=vector)
                new += 1
    return {"new": new, "updated": updated, "skipped": skipped}


def collect_schedule(month=None):
    """해당 월의 경기 일정을 수집하고 관계형 DB와 벡터 DB에 저장한다."""
    _setup()
    from django.utils import timezone
    from tving.parsers import parse_calendar, parse_schedule
    from tving.relational import persist_month
    from tving.service import _provider_json
    # 월이 전달되지 않으면 현재 한국 시간 기준 연-월을 사용한다.
    month = month or datetime.now(KST).strftime("%Y-%m")
    # 해당 월에 경기가 있는 날짜 목록을 조회한다.
    calendar = parse_calendar(
        _provider_json(
            "/kbo/schedule/day",
            {"date": month.replace("-", "")},
        ),
        month,
    )
    games, days = [], []
    for day in calendar:
        # 날짜별 실제 경기 정보를 조회한다.
        date = f"{month}-{day:02d}"
        parsed = parse_schedule(
            _provider_json(
                "/kbo/schedule",
                {"date": date.replace("-", "")},
            ),
            date,
        )
        games.extend(parsed)
        # 날짜별 수집 상태를 기록한다.
        days.append({
            "date": date,
            "status": "ready" if parsed else "empty",
            "gameCount": len(parsed),
        })
    # 관계형 DB에 저장할 월간 데이터 구성
    data = {
        "year": int(month[:4]),
        "month": month,
        "today": datetime.now(KST).date().isoformat(),
        "games": games,
        "days": days,
        "loading": False,
    }
    # 테스트/수동 실행 시 외부 데이터 동기화 간격 제한에 걸리지 않도록 비활성화한다.
    with override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=0):
        persist_month(data, timezone.now())
    # 벡터 DB에 저장할 경기 문서 생성
    docs = []
    for g in games:
        stadium_code = STADIUM_MAP.get(g["stadium"], g["stadium"])
        away_code = TEAM_MAP.get(g["away"]["code"], g["away"]["code"])
        home_code = TEAM_MAP.get(g["home"]["code"], g["home"]["code"])
        status_label = g.get("statusLabel", g["status"])
        score_text = ""
        if g["away"].get("score") is not None and g["home"].get("score") is not None:
            if g["away"]["score"] == g["home"]["score"]:
                result_text = "무승부입니다"
            else:
                winner = g["away"]["name"] if g["away"]["score"] > g["home"]["score"] else g["home"]["name"]
                result_text = f"{winner}가 승리했습니다"
            score_text = f" 최종 스코어는 {g['away']['name']} {g['away']['score']} - {g['home']['name']} {g['home']['score']}로 {result_text}."
        cancel_text = " 경기 취소 정보가 있습니다." if g["status"] == "cancelled" else ""
        season_val = g["date"][:4]
        identity = "|".join([season_val, g["date"].replace("-", "") + g["time"].replace(":", ""), g["away"]["name"], g["home"]["name"], g["stadium"]])
        natural = f"schedule_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
        content = (
            f"{season_val} 시즌 KBO 경기 일정입니다. {g['date']} {g['time']}에 {g['stadium']}구장에서 "
            f"{g['away']['name']} 원정팀과 {g['home']['name']} 홈팀이 경기를 진행합니다. "
            f"경기 상태는 {status_label}입니다.{score_text}{cancel_text}"
        )
        metadata = {
            "id": natural, "source": "https://gw.tving.com/bff/sports/v2/kbo/schedule", "team": f"{g['away']['name']}{g['home']['name']}",
            "category": "SCHEDULE", "updated_at": datetime.now(KST).isoformat(), "content": content,
            "source_file": "kbo_schedule_full.csv", "game_code": g["id"], "game_date": g["date"],
            "game_time": g["time"], "stadium_code": stadium_code, "stadium_name_raw": g["stadium"],
            "away_team_code": away_code, "home_team_code": home_code,
            "away_score": g["away"].get("score"), "home_score": g["home"].get("score"),
            "status_code": g["status"], "game_type": "REGULAR", "evidence_type": "THIRD_PARTY_API",
            "status_tag": "CONFIRMED" if g["status"] in ("scheduled", "live", "final") else g["status"].upper(),
        }
        scope = stadium_code or away_code or home_code or "COMMON"
        content = f"[{STADIUM_NAMES.get(stadium_code, g['stadium'])}] {content}"
        metadata["content"] = content
        docs.append({"doc_id": f"SCHEDULE_{scope}_{natural}", "content": content, "metadata": metadata})
    # 경기 문서를 임베딩하고 pgvector에 저장한다.
    return len(games), _vector_upsert(docs)


def collect_standing():
    """현재 시즌 순위와 선수 순위를 수집하고 관계형 DB와 벡터 DB에 저장한다."""
    _setup()
    from django.utils import timezone
    from tving.parsers import parse_rankings, parse_schedule, parse_standings
    from tving.service import _provider_json
    from tving.relational import persist_daily
    # 현재 한국 시간 기준 연도와 수집 시각
    year, now = str(datetime.now(KST).year), timezone.now()
    # 오늘 날짜를 KBO API 조회 기준으로 사용한다.
    day = now.astimezone(KST).date().isoformat()
    # 현재 시즌 팀 순위 수집
    rows = parse_standings(
        _provider_json(
            "/kbo/history/team",
            {
                "yearSeason": year,
                "gameSeason": "0",
            },
        ),
        day,
    )
    # 투수 / 타자 개인 순위를 각각 조회한다.
    rankings = {
        "pitchers": parse_rankings(
            _provider_json(
                "/kbo/history/athlete/ranking",
                {
                    "yearSeason": year,
                    "gameSeason": "regular",
                    "athleteType": "pitcher",
                    "pitcherRankOrder": "earnedRunAverage",
                    "screenCode": "CSSD0100",
                    "osCode": "CSOD0900",
                },
            ),
            "pitcher",
        ),
        "hitters": parse_rankings(
            _provider_json(
                "/kbo/history/athlete/ranking",
                {
                    "yearSeason": year,
                    "gameSeason": "regular",
                    "athleteType": "hitter",
                    "hitterRankOrder": "battingAverage",
                    "screenCode": "CSSD0100",
                    "osCode": "CSOD0900",
                },
            ),
            "hitter",
        ),
    }
    # 오늘 경기 일정도 함께 수집한다.
    games = parse_schedule(
        _provider_json(
            "/kbo/schedule",
            {"date": day.replace("-", "")},
        ),
        day,
    )
    # 관계형 DB에 일일 데이터 저장
    persist_daily(
        {
            "date": day,
            "games": games,
            "standings": rows,
            "individualRankings": rankings,
        },
        now,
    )
    docs = []
    for row in rows:
        # 팀별 순위를 검색할 수 있도록 벡터 문서 형태로 변환한다.
        team_code = TEAM_MAP.get(row["teamCode"], row["teamCode"])
        stadium_code = TEAM_HOME.get(team_code)
        content = (
            f"{year} 시즌 KBO 순위에서 {row['team']}는 {row['rank']}위입니다. 총 {row['played']}경기를 치렀으며 "
            f"{row['wins']}승 {row['losses']}패 {row['draws']}무를 기록했습니다. 승률은 {row['winRate']}이고 "
            f"게임차는 {row['gamesBehind']}입니다."
        )
        identity = f"{year}_{row['team']}"
        natural = f"standing_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
        metadata = {
            "id": natural,
            "source": "https://gw.tving.com/bff/sports/v2/kbo/history/team", "team": row["team"],
            "category": "STANDING", "updated_at": now.isoformat(), "content": content,
            "source_file": "kbo_standing.csv", "team_code": team_code, "stadium_code": stadium_code,
            "rank": row["rank"], "played": row["played"], "games": row["played"],
            "wins": row["wins"], "losses": row["losses"], "draws": row["draws"],
            "winRate": row["winRate"], "win_rate": row["winRate"],
            "gamesBehind": row["gamesBehind"], "games_behind": row["gamesBehind"],
            "snapshot_date": day,
        }
        scope = stadium_code or team_code or "COMMON"
        content = f"[{STADIUM_NAMES.get(stadium_code, stadium_code)} · {TEAM_KO.get(team_code, row['team'])}] {content}"
        metadata["content"] = content
        docs.append({"doc_id": f"STANDING_{scope}_{natural}", "content": content, "metadata": metadata})
    # 팀 순위 문서를 임베딩하여 pgvector에 저장한다.
    return len(rows), _vector_upsert(docs)
