"""
KBO 시즌 전체 일정 수집 스크립트 (원본 kbo_schedule_crawler.py 확장판)

원본 스크립트와의 차이점:
1. "이번 달 남은 경기"만 가져오던 것을 "정규시즌 개막일(SEASON_START)부터 오늘까지 지난 경기 + 앞으로 남은 경기"
   전체로 확장 (여러 달을 순회).
2. 지난 경기(status=END)는 결과 스코어까지 content에 포함.
3. 팀명을 표준 team_code(LG/DOOSAN/KIWOOM/SSG/KT/HANWHA/SAMSUNG/KIA/LOTTE/NC)로,
   구장명을 표준 stadium_code(JAMSIL/GOCHEOK/...)로 매핑한 컬럼을 추가.
4. 시범경기(개막일 이전, 3/12~3/24 등)와 올스타전(팀코드가 WE/EA로 나오는 특수 경기)은
   순위에 영향이 없는 경기라 자동으로 제외.
5. 결과 파일명을 kbo_schedule.csv가 아니라 kbo_schedule_full.csv로 분리 저장
   (기존 kbo_schedule.csv/파이프라인은 그대로 두고 건드리지 않기 위함).

주의: 정규시즌 개막일(SEASON_START)과 시즌 마지막 달(SEASON_END_MONTH)은
매 시즌 바뀌므로, 다음 시즌에 재사용할 때는 이 두 값을 확인 후 수정해야 한다.
"""

import requests
import pandas as pd
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# 0 8 * 3-10 * /usr/bin/python3 /path/to/your/script.py   (매일 아침 한 번이면 충분 — 지난 경기 결과 갱신 목적)

# 실행 위치 무관하게 항상 baseball_data_organized/data/preprocessed로 저장되도록
# 이 스크립트(crawling 폴더) 기준 상대 경로로 고정 (kbo_standing_crawler.py와 동일한 방식,
# data/raw=원본만, data/preprocessed=크롤링/가공 결과물로 재정리함)
SCRIPT_DIR = Path(__file__).parent
SCHEDULE_OUTPUT = SCRIPT_DIR / ".." / "data" / "preprocessed" / "kbo_schedule_full.csv"

KST = ZoneInfo("Asia/Seoul")
now = datetime.now(KST)
updated_at_str = now.strftime("%Y-%m-%d %H:%M:%S")

SEASON_YEAR = 2026
SEASON_START = datetime(2026, 3, 28, tzinfo=KST)   # 2026 KBO 정규시즌 개막일
SEASON_MONTHS = ["03", "04", "05", "06", "07", "08", "09", "10"]  # 순회할 달 목록

REAL_TEAM_CODES = {"LG", "OB", "WO", "SK", "KT", "HH", "SS", "HT", "LT", "NC"}

TEAM_CODE_MAP = {
    "LG": "LG", "OB": "DOOSAN", "WO": "KIWOOM", "SK": "SSG", "KT": "KT",
    "HH": "HANWHA", "SS": "SAMSUNG", "HT": "KIA", "LT": "LOTTE", "NC": "NC",
}
STADIUM_MAP = {
    "잠실": "JAMSIL", "고척": "GOCHEOK", "문학": "MUNHAK", "수원": "SUWON",
    "대전": "DAEJEON", "대구": "DAEGU", "광주": "GWANGJU", "사직": "SAJIK",
    "창원": "CHANGWON",
}
STATUS_TEXT = {"PREV": "경기 전", "END": "경기 종료", "LIVE": "경기 중", "CANCEL": "경기 취소"}

headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.tving.com/"
}


def josa_i_ga(word: str) -> str:
    """단어 끝 글자에 받침이 있으면 '이', 없으면 '가'."""
    if not word:
        return "가"
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        return "이" if (code - 0xAC00) % 28 != 0 else "가"
    return "가"


def get_schedule(date: str):
    url = (
        f"https://gw.tving.com/bff/sports/v2/kbo/schedule"
        f"?date={date}&screenCode=CSSD0100&osCode=CSOD0900"
    )
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    bands = response.json().get("data", {}).get("bands", [])
    return bands[0] if bands else {}


all_games = []

for month in SEASON_MONTHS:
    year_month = f"{SEASON_YEAR}{month}"
    try:
        game_days = get_schedule(f"{year_month}01").get("calendar", [])
    except Exception as e:
        print(f"{year_month} 캘린더 조회 실패: {e}")
        continue

    for day in game_days:
        target_date = f"{year_month}{day:02d}"

        try:
            schedule_data = get_schedule(target_date)
            items = schedule_data.get("items", [])

            for item in items:
                dt = str(item.get("dateTime", ""))
                if len(dt) != 12:
                    continue

                game_dt = datetime.strptime(dt, "%Y%m%d%H%M").replace(tzinfo=KST)

                away = item.get("away", {})
                home = item.get("home", {})
                away_raw_code, home_raw_code = away.get("code", ""), home.get("code", "")

                # 올스타전(WE/EA 등 팀코드가 아닌 경기) 제외
                if away_raw_code not in REAL_TEAM_CODES or home_raw_code not in REAL_TEAM_CODES:
                    continue
                # 정규시즌 개막 이전 시범경기 제외
                if game_dt < SEASON_START:
                    continue

                away_team, home_team = away.get("name", ""), home.get("name", "")
                stadium_raw = item.get("stadium", "")
                status = item.get("status", "")
                status_text = STATUS_TEXT.get(status, status)

                formatted_dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[8:10]}:{dt[10:12]}"

                identity = "|".join([str(SEASON_YEAR), dt, away_raw_code, home_raw_code, stadium_raw])
                hash_value = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                doc_id = f"schedule_{hash_value[:16]}"

                if status == "END":
                    a_score, h_score = away.get("score", 0), home.get("score", 0)
                    if a_score > h_score:
                        winner_text = f"{away_team}{josa_i_ga(away_team)} 승리했습니다."
                    elif h_score > a_score:
                        winner_text = f"{home_team}{josa_i_ga(home_team)} 승리했습니다."
                    else:
                        winner_text = "무승부로 종료됐습니다."
                    content_text = (
                        f"{SEASON_YEAR} 시즌 KBO 경기 일정입니다. {formatted_dt}에 {stadium_raw}구장에서 "
                        f"{away_team} 원정팀과 {home_team} 홈팀이 경기를 진행합니다. "
                        f"경기 상태는 {status_text}입니다. "
                        f"최종 스코어는 {away_team} {a_score} - {home_team} {h_score}로 {winner_text}"
                    )
                elif status == "CANCEL":
                    a_score = h_score = ""
                    content_text = (
                        f"{SEASON_YEAR} 시즌 KBO 경기 일정입니다. {formatted_dt}에 {stadium_raw}구장에서 "
                        f"{away_team} 원정팀과 {home_team} 홈팀의 경기가 예정되어 있었으나 취소되었습니다."
                    )
                else:
                    a_score = h_score = ""
                    content_text = (
                        f"{SEASON_YEAR} 시즌 KBO 경기 일정입니다. {formatted_dt}에 {stadium_raw}구장에서 "
                        f"{away_team} 원정팀과 {home_team} 홈팀이 경기를 진행합니다. "
                        f"경기 상태는 {status_text}입니다."
                    )

                all_games.append({
                    "id": doc_id,
                    "source": (
                        f"https://gw.tving.com/bff/sports/v2/kbo/schedule"
                        f"?date={target_date}&screenCode=CSSD0100&osCode=CSOD0900"
                    ),
                    "team": f"{away_team}{home_team}",
                    "category": "schedule",
                    "updated_at": updated_at_str,
                    "content": content_text,
                    "game_date": game_dt.strftime("%Y-%m-%d"),
                    "game_time": game_dt.strftime("%H:%M"),
                    "stadium_code": STADIUM_MAP.get(stadium_raw, "OTHER"),
                    "stadium_name_raw": stadium_raw,
                    "away_team_code": TEAM_CODE_MAP.get(away_raw_code, away_raw_code),
                    "home_team_code": TEAM_CODE_MAP.get(home_raw_code, home_raw_code),
                    "away_score": a_score,
                    "home_score": h_score,
                    "status_code": status,
                    "game_type": "REGULAR",
                    "evidence_type": "THIRD_PARTY_API",
                    "status_tag": "CONFIRMED",
                })

        except Exception as e:
            print(f"{target_date} 수집 실패: {e}")

# CSV 저장
if all_games:
    file_name = SCHEDULE_OUTPUT
    cols = ["id", "source", "team", "category", "updated_at", "content",
            "game_date", "game_time", "stadium_code", "stadium_name_raw",
            "away_team_code", "home_team_code", "away_score", "home_score",
            "status_code", "game_type", "evidence_type", "status_tag"]

    df = pd.DataFrame(all_games)[cols].drop_duplicates(subset=["id"])
    df.to_csv(file_name, index=False, encoding="utf-8-sig")

    print(f"CSV 저장 완료: {file_name}")
    print(f"수집된 경기 수: {len(df)}")
else:
    print("수집된 경기 일정이 없습니다.")
