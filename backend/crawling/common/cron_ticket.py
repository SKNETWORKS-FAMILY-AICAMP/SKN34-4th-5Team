import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from django.db import transaction

# 크롤링 날짜/시간을 한국 시간 기준으로 처리
KST = ZoneInfo("Asia/Seoul")

# 팀 이름/코드 표준화
TEAM_CODES = {"LG": "LG", "한화": "HH", "SSG": "SK", "삼성": "SS", "NC": "NC", "KT": "KT", "롯데": "LT", "KIA": "HT", "두산": "OB", "키움": "WO"}

# 팀 코드 → 홈구장 코드 매핑
TEAM_STADIUMS = {"LG": "JAMSIL", "DOOSAN": "JAMSIL", "HH": "DAEJEON", "SSG": "MUNHAK", "SS": "DAEGU", "NC": "CHANGWON", "KT": "SUWON", "LT": "SAJIK", "HT": "GWANGJU", "WO": "GOCHEOK"}


def _setup():
    """독립 실행 스크립트에서 Django ORM을 사용할 수 있도록 Django를 초기화한다."""
    # 현재 파일 기준 프로젝트 루트 경로
    root = Path(__file__).resolve().parents[2]
    # 프로젝트 루트를 Python 모듈 검색 경로에 추가
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Django 설정 모듈 지정
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Django 초기화
    import django
    django.setup()


def _events(html):
    """페이지 HTML에 포함된 initialEvents JSON 배열을 추출한다."""
    marker = r'initialEvents\":['
    start = html.find(marker)
    if start < 0:
        raise ValueError("예매 이벤트를 찾지 못했습니다.")
    # '[' 시작 위치로 이동
    start += len(marker) - 1
    depth = 0
    quoted = escaped = False
    # JSON 배열의 끝 위치를 직접 찾는다.
    # 문자열 내부의 []는 배열 깊이에 포함하지 않는다.
    for index in range(start, len(html)):
        char = html[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            # 중괄호가 아니라 배열 []의 깊이만 추적한다.
            depth += char == "["
            depth -= char == "]"
            # 처음 배열이 닫히는 위치
            if depth == 0:
                return json.loads(html[start:index + 1].replace(r'\"', '"'))
    raise ValueError("예매 이벤트 종료 위치를 찾지 못했습니다.")


def _policy_content(event):
    """예매 이벤트에서 LLM/RAG 및 DB 저장에 사용할 표준 텍스트를 만든다."""
    title = event.get("title", "")
    description = event.get("description", "")
    try:
        # 이벤트 시간을 한국 시간으로 변환
        date = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(KST)
        date_text = f"{date.month}월 {date.day}일 {('월','화','수','목','금','토','일')[date.weekday()]}요일"
        # 제목의 날짜 표현을 실제 예매 날짜로 보정
        title = re.sub(r"(오전|오후)\s\*\d+시", f"{date_text} \\g<0>", title, count=1)
        # 설명의 오픈 시간도 같은 방식으로 보정
        description = re.sub(r"(오픈:\s\*)(오전|오후)\s\*\d+시", f"\\g<1>{date_text} \\g<2>", description, count=1)
    except (KeyError, ValueError):
        # 날짜 형식이 잘못된 경우 원본 제목/설명을 그대로 사용
        pass
    # 여러 공백을 하나로 정리해 저장용 텍스트 생성
    return re.sub(r"\s+", " ", f"{title} {description}").strip()


def _parse_policy(content, team_code):
    """크롤링 원문과 기존 구조화 CSV 양쪽 형식을 허용한다."""
    from preprocessing.parse_ticket_policy import make_new_id, parse_row
    # 기존 전처리 파서를 먼저 사용한다.
    parsed = parse_row(content)
    # 정상적으로 파싱되면 기존 ID 생성 규칙을 사용한다.
    if parsed.get("parse_status") == "OK":
        return parsed, make_new_id(parsed, team_code, content)
    # 기존 파싱에 실패하면 정규식으로 최소한의 정보를 추출한다.
    policy_type = re.search(r"^\[(선예매|일반)\]", content)
    booking = re.search(r"예매처:\s\*(.*)$", content)
    maximum = re.search(r"(?:최대|시즌)\s\*(\d+)매", content)
    name = re.search(r"^\[[^]]+\]\s\*(.*?)\s+\d{1,2}월\s\*\d{1,2}일", content)
    parsed = {
        "parse_status": "OK" if policy_type else "FAILED",
        "policy_type": policy_type.group(1) if policy_type else "",
        "policy_name": name.group(1).strip() if name else "",
        "policy_subtype": "",
        "max_tickets": maximum.group(1) if maximum else "",
        "booking_channel_and_condition": booking.group(1).strip() if booking else "",
    }
    # 주요 필드를 조합해서 내용이 같은 정책은 같은 ID를 만들 수 있게 한다.
    key = "|".join((team_code, parsed["policy_type"], parsed["policy_name"], parsed["max_tickets"], content))
    # MD5 앞 16자리만 사용해 짧은 정책 ID 생성
    return parsed, f"ticket_policy_{hashlib.md5(key.encode()).hexdigest()[:16]}"


def _collect_policies():
    """현재 월의 예매 이벤트를 크롤링하고 예매 정책 데이터만 추출한다."""
    import requests
    from preprocessing.parse_ticket_policy import make_new_id, parse_row
    now = datetime.now(KST)
    # yagu.today의 현재 월 캘린더 페이지 요청
    response = requests.get(f"https://yagu.today/calendar/{now.year}/{now.month}", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    rows = []
    # 페이지에서 이벤트 목록 추출
    for event in _events(response.text):
        # 예매 이벤트만 대상으로 한다.
        if event.get("category") != "ticket":
            continue
        # 홈팀 또는 원정팀을 기준으로 팀 코드 결정
        code = TEAM_CODES.get(event.get("homeTeam")) or TEAM_CODES.get(event.get("awayTeam"))
        # 팀을 식별하지 못하면 해당 데이터는 제외
        if not code:
            continue
        try:
            # 이벤트 날짜를 한국 시간으로 변환
            event_date = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(KST)
        except (KeyError, ValueError):
            # 날짜를 읽을 수 없는 이벤트는 제외
            continue
        # 이미 지난 예매 이벤트는 수집 대상에서 제외
        if event_date < now:
            continue
        # 검색/저장용 표준 텍스트 생성
        content = _policy_content(event)
        # 구조화된 예매 정책 정보와 고유 ID 생성
        parsed, policy_id = _parse_policy(content, code)
        rows.append({"id": policy_id, "team_code": code, "content": content, "parsed": parsed})
    return rows


def _load_prices(now):
    """CSV의 구장 티켓 가격 데이터를 관계형 DB에 반영한다."""
    from baseball.data_loader import stable_id
    from baseball.models import HomeContext, SeatZone, TicketPrice
    # Docker 환경의 기본 경로
    path = Path("/data/preprocessed/구장티켓가격.csv")
    # 로컬 실행 시 프로젝트 내부 데이터 경로 사용
    if not path.exists():
        path = Path(__file__).resolve().parents[3] / "data/preprocessed/구장티켓가격.csv"
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            # 시즌 + 팀 + 구장으로 홈구장 컨텍스트 조회
            context = HomeContext.objects.filter(season=int(row["season"]), team__team_code=row["team_code"], stadium__stadium_code=row["stadium_code"]).first()
            # 해당 좌석 구역 조회
            zone = SeatZone.objects.filter(home_context=context, zone_code=row["zone_code"]).first() if context else None
            # 연결되는 구장/좌석 정보가 없으면 가격 데이터도 저장하지 않는다.
            if not zone:
                continue
            # 안정적인 ID 생성에 사용할 전체 데이터 조합
            key = "|".join(row.get(name, "") for name in ("season", "team_code", "stadium_code", "zone_code", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition"))
            values = {"seat_zone": zone, "price_tier": row["price_tier"], "day_type": row["day_type"], "customer_type": row["customer_type"], "group_size": int(row["group_size"]) if row["group_size"].isdigit() else None, "price_krw": int(row["price_krw"]), "valid_from": row["valid_from"] or None, "valid_to": row["valid_to"] or None, "discount_condition": row["discount_condition"], "collected_at": now}
            # 변경 여부 확인에 사용하는 비교 필드
            lookup = {field: values[field] for field in ("seat_zone", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition")}
            # 동일한 가격 조건의 기존 데이터 검색
            item = TicketPrice.objects.filter(**lookup).order_by("id").first()
            if item is None:
                # 기존 데이터가 없으면 새로 생성
                item = TicketPrice.objects.create(id=stable_id(TicketPrice, key), **values)
            else:
                # 기존 데이터가 있으면 수집 시각 등을 포함해 최신 값으로 갱신
                for field, value in values.items(): setattr(item, field, value)
                item.save(update_fields=tuple(values))
            count += 1
    return count


def _deduplicate_ticket_rows():
    """티켓 가격/정책/벡터 문서의 중복 데이터를 정리한다."""
    from baseball.models import TicketPolicy, TicketPrice
    from llm.models import DocumentChunk
    # --------------------------------
    # 티켓 가격 중복 제거
    # --------------------------------
    removed_prices = 0
    # 가격이 동일하다고 판단할 필드
    price_fields = ("seat_zone_id", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition")
    seen = {}
    for item in TicketPrice.objects.order_by("id"):
        # 동일한 가격 조건을 하나의 키로 만든다.
        key = tuple(getattr(item, field) for field in price_fields)
        if key in seen:
            # 이미 동일한 데이터가 있으면 현재 데이터를 삭제
            item.delete()
            removed_prices += 1
        else:
            seen[key] = item.id
    # --------------------------------
    # 예매 정책 중복 제거
    # --------------------------------
    removed_policies = 0
    seen = {}
    for item in TicketPolicy.objects.exclude(channel_condition="").order_by("id"):
        # 예매처/팀/정책 조건이 같은 데이터를 중복으로 판단
        key = (item.channel_condition, item.team_id, item.policy_type, item.subtype, item.max_tickets, item.channel_no)
        if key in seen:
            item.delete()
            removed_policies += 1
        else:
            seen[key] = item.id
    # --------------------------------
    # 벡터 문서 중복 제거
    # --------------------------------
    removed_vectors = 0
    seen = {}
    for item in DocumentChunk.objects.filter(metadata__category="TICKET_POLICY").order_by("id"):
        # 본문 해시가 같으면 동일한 벡터 문서로 판단
        key = hashlib.sha256(item.content.encode()).hexdigest()
        if key in seen:
            item.delete()
            removed_vectors += 1
        else:
            seen[key] = item.id
    return removed_prices, removed_policies, removed_vectors


def collect_tickets():
    """예매 정책/가격을 수집하고 관계형 DB와 벡터 DB를 갱신한다."""
    _setup()
    from baseball.models import TicketPolicy
    from tving.relational import team_for
    from .cron_tving import _vector_upsert
    now = datetime.now(KST)
    # 외부 페이지에서 현재 유효한 예매 정책 수집
    policies = _collect_policies()
    # 관계형 DB 변경을 하나의 트랜잭션으로 처리
    with transaction.atomic():
        # 기존 DB에 쌓인 중복 데이터 정리
        removed_prices, removed_policies, removed_vectors = _deduplicate_ticket_rows()
        # --------------------------------
        # 기존 TicketPolicy 데이터 보정
        # --------------------------------
        for item in TicketPolicy.objects.filter(policy_type=""):
            # 기존 정책도 동일한 파서로 구조화
            parsed, _ = _parse_policy(item.channel_condition, "")
            # 파싱에 실패하면 그대로 둔다.
            if parsed.get("parse_status") != "OK":
                continue
            # 구조화된 필드 반영
            item.policy_type = parsed.get("policy_type", "")
            item.subtype = parsed.get("policy_subtype", "")
            item.max_tickets = int(parsed["max_tickets"]) if parsed.get("max_tickets", "").isdigit() else None
            item.booking_channel = parsed.get("booking_channel_and_condition", "")
            item.save(update_fields=("policy_type", "subtype", "max_tickets", "booking_channel"))
        # --------------------------------
        # 새로 크롤링한 예매 정책 저장/갱신
        # --------------------------------
        for row in policies:
            # 내부 팀 객체 조회
            team = team_for(row["team_code"])
            # 팀 매핑이 없으면 저장하지 않는다.
            if not team:
                continue
            parsed = row["parsed"]
            max_tickets = parsed.get("max_tickets") or None
            values = {"policy_code": row["id"], "team": team, "game": None, "policy_type": parsed.get("policy_type", ""), "subtype": parsed.get("policy_subtype", ""), "open_at": None, "max_tickets": int(max_tickets) if max_tickets and max_tickets.isdigit() else None, "channel_no": 1, "booking_channel": parsed.get("booking_channel_and_condition", ""), "channel_condition": row["content"], "collected_at": now}
            # 먼저 생성 ID 기준으로 기존 데이터를 찾는다.
            item = TicketPolicy.objects.filter(policy_code=row["id"], channel_no=1).first()
            # ID로 찾지 못하면 본문 기준으로 한 번 더 찾는다.
            if not item:
                item = TicketPolicy.objects.filter(channel_condition=row["content"], channel_no=1).first()
            if item:
                # 기존 정책이면 최신 데이터로 갱신
                for field, value in values.items(): setattr(item, field, value)
                item.save(update_fields=tuple(values))
            else:
                # 신규 정책이면 생성
                item = TicketPolicy.objects.create(id=abs(hash(row["id"])) % 2_000_000_000, **values)
        # CSV의 티켓 가격 데이터도 관계형 DB에 반영
        prices = _load_prices(now)
    # --------------------------------
    # 벡터 DB용 문서 생성
    # --------------------------------
    docs = []
    for row in policies:
        # 팀 코드로 홈구장 코드 결정
        stadium = TEAM_STADIUMS.get(row["team_code"], "UNKNOWN")
        docs.append({
            "doc_id": f"TICKET_POLICY_{stadium}_{row['id']}",
            "content": row["content"],
            "metadata": {
                "id": row["id"], "source": "https://yagu.today/calendar", "team": row["team_code"],
                "category": "TICKET_POLICY", "source_file": "kbo_ticket_policy_structured.csv",
                "team_code": row["team_code"], "stadium_code": stadium, "content": row["content"],
            },
        })
    # 예매 정책을 임베딩하여 pgvector에 저장/갱신
    vector_result = _vector_upsert(docs)
    # 크롤링/DB/벡터 처리 결과를 한 번에 반환
    return len(policies), prices, {"removed_prices": removed_prices, "removed_policies": removed_policies, "removed_vectors": removed_vectors, **vector_result}
