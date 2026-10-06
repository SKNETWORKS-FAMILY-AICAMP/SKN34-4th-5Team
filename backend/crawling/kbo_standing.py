import requests
import pandas as pd
import hashlib
import os
from datetime import datetime
from pathlib import Path

# 0 23 * 3-10 * /usr/bin/python3 /path/to/your/script.py

# 실행 위치 무관하게 항상 baseball_data_organized/data/preprocessed로 저장되도록
# 이 스크립트(crawling 폴더) 기준 상대 경로로 고정한다.
# (data/raw = 손대지 않는 원본만, data/preprocessed = 크롤링/가공 결과물로 재정리함.
#  kbo_standing.csv는 실행할 때마다 덮어써지는 생성물이라 raw가 아니라 preprocessed에 둔다.)
SCRIPT_DIR = Path(__file__).parent
STANDING_OUTPUT = SCRIPT_DIR / ".." / "data" / "preprocessed" / "kbo_standing.csv"
HISTORY_OUTPUT = SCRIPT_DIR / ".." / "data" / "preprocessed" / "kbo_standing_history.csv"

# [kbo_standing.csv 컬럼 — 원본과 동일, 최신 스냅샷 1개만 유지]
# - id / source / team / category / updated_at / content

# [kbo_standing_history.csv 컬럼 — 이번에 추가된 이력 누적 파일]
# - snapshot_date / team_code / team_name_ko / rank / games / wins / losses / draws
# - win_rate / games_behind / winning_streak / batting_avg / era / recent_games
# - source / status / evidence_type / collected_at

# ==== 원본 스크립트와 동일 ====

# 1. 현재 연도 / 월 / 수집 일시
now = datetime.now()
current_year = now.strftime("%Y")
current_year_month = now.strftime("%Y%m")
updated_at_str = now.strftime("%Y-%m-%d %H:%M:%S")

# 2. TVING KBO 순위 API
url = (
    f"https://gw.tving.com/bff/sports/v2/kbo/history/team"
    f"?yearSeason={current_year}"
    f"&gameSeason=0"
    f"&screenCode=CSSD0100"
    f"&osCode=CSOD0900"
)

headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.tving.com/"
}

# team_stadium_code_map.csv 표준 코드로 변환 (TVING 응답의 한글 팀명 -> 표준 코드)
TEAM_NAME_TO_CODE = {
    "LG": "LG", "두산": "DOOSAN", "키움": "KIWOOM", "SSG": "SSG", "KT": "KT",
    "한화": "HANWHA", "삼성": "SAMSUNG", "KIA": "KIA", "롯데": "LOTTE", "NC": "NC",
}

all_standings = []

# 3. 데이터 수집
try:
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    res_data = response.json()

    bands = res_data.get("data", {}).get("bands", [])
    items = bands[0].get("items", []) if bands else []

    # 4. 팀별 데이터 생성
    for item in items:
        rank = item.get("rank", "")
        team_name = item.get("name", "")
        game_count = item.get("games", 0)
        win = item.get("wins", 0)
        loss = item.get("losses", 0)
        draw = item.get("draws", 0)
        win_rate = item.get("winningPercentage", "")
        game_behind = item.get("gamesBehind", "-")

        # ID 생성: 팀 + 시즌 기준 (원본과 동일 — 최신 스냅샷용 ID라 날짜는 안 들어감)
        identity = f"{current_year}_{team_name}"
        hash_value = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        doc_id = f"standing_{hash_value[:16]}"

        # RAG용 content
        content_text = (
            f"{current_year} 시즌 KBO 순위에서 "
            f"{team_name}는 {rank}위입니다. "
            f"총 {game_count}경기를 치렀으며 "
            f"{win}승 {loss}패 {draw}무를 기록했습니다. "
            f"승률은 {win_rate}이고 게임차는 {game_behind}입니다."
        )

        all_standings.append({
            "id": doc_id,
            "source": url,
            "team": team_name,
            "category": "standing",
            "updated_at": updated_at_str,
            "content": content_text,
            # ---- 이력 누적용으로 추가 수집한 필드 (원본 API 응답엔 이미 있었는데 원본 스크립트가 안 쓰던 값들) ----
            "team_name_ko": team_name,
            "team_code": TEAM_NAME_TO_CODE.get(team_name, team_name),
            "rank": rank,
            "games": game_count,
            "wins": win,
            "losses": loss,
            "draws": draw,
            "win_rate": win_rate,
            "games_behind": game_behind,
            "winning_streak": item.get("winningStreak", ""),
            "batting_avg": item.get("battingAverage", ""),
            "era": item.get("earnedRunAverage", ""),
            "recent_games": item.get("recentGames", ""),
        })

except Exception as e:
    print(f"순위 데이터 수집 실패: {e}")

# 5. CSV 저장 (원본과 동일 — 오늘자 최신 스냅샷 1개, 매번 덮어씀)
if all_standings:
    file_name = STANDING_OUTPUT

    # 원본 6개 컬럼만 저장해서 팀 파이프라인 호환성 유지 (기존 kbo_standing.csv 그대로)
    base_cols = ["id", "source", "team", "category", "updated_at", "content"]
    pd.DataFrame(all_standings)[base_cols].to_csv(
        file_name,
        index=False,
        encoding="utf-8"
    )

    print(f"CSV 저장 완료: {file_name}")
    print(f"수집된 팀 수: {len(all_standings)}")

    # ==== 여기부터 새로 추가된 부분: 이력 누적 (kbo_standing_history.csv) ====
    # kbo_standing.csv는 원본 그대로 "최신 스냅샷"용으로 유지하고,
    # 별도 파일에 날짜별로 계속 append해서 순위 흐름(2-6 항목)을 볼 수 있게 한다.
    history_file = HISTORY_OUTPUT
    today_str = now.strftime("%Y-%m-%d")

    history_cols = [
        "snapshot_date", "team_code", "team_name_ko", "rank", "games", "wins", "losses",
        "draws", "win_rate", "games_behind", "winning_streak", "batting_avg", "era",
        "recent_games", "source", "status", "evidence_type", "collected_at",
    ]

    # 오늘 날짜 스냅샷이 이미 있으면 중복 저장하지 않음 (하루 여러 번 실행해도 안전)
    already_has_today = False
    if os.path.exists(history_file):
        existing = pd.read_csv(history_file, encoding="utf-8-sig")
        if "snapshot_date" in existing.columns and today_str in existing["snapshot_date"].astype(str).values:
            already_has_today = True

    if already_has_today:
        print(f"[이력 누적] {today_str} 스냅샷은 이미 존재해서 건너뜁니다.")
    else:
        history_rows = []
        for s in all_standings:
            history_rows.append({
                "snapshot_date": today_str,
                "team_code": s["team_code"],
                "team_name_ko": s["team_name_ko"],
                "rank": s["rank"],
                "games": s["games"],
                "wins": s["wins"],
                "losses": s["losses"],
                "draws": s["draws"],
                "win_rate": s["win_rate"],
                "games_behind": s["games_behind"],
                "winning_streak": s["winning_streak"],
                "batting_avg": s["batting_avg"],
                "era": s["era"],
                "recent_games": s["recent_games"],
                "source": s["source"],
                "status": "CONFIRMED",
                "evidence_type": "THIRD_PARTY_API",
                "collected_at": updated_at_str,
            })

        history_df = pd.DataFrame(history_rows)[history_cols]
        write_header = not os.path.exists(history_file)
        history_df.to_csv(
            history_file,
            mode="a",
            header=write_header,
            index=False,
            encoding="utf-8-sig",
        )
        print(f"[이력 누적] {today_str} 스냅샷 {len(history_rows)}건을 {history_file}에 추가했습니다.")
else:
    print("수집된 순위 데이터가 없습니다.")
