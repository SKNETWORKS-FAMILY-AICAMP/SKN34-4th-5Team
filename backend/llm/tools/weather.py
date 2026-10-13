"""weather domain tools."""
from datetime import date, datetime, timedelta
from typing import Annotated

from langchain_core.tools import ToolException
from pydantic import Field, StringConstraints

from .common import ToolInput, _tool

DEFAULT_TIME = "18:30"  # 경기 시각을 모를 때 다른 날짜의 기준 시각(평일 경기 시작 시각)
PRECIPITATION = frozenset({"비", "비/눈", "눈", "소나기"})  # 서비스 label 은 강수가 있으면 강수, 없으면 하늘 상태


class WeatherInput(ToolInput):
    stadium_code: str = Field(pattern="^(JAMSIL|GOCHEOK|MUNHAK|SUWON|DAEJEON|DAEGU|GWANGJU|SAJIK|CHANGWON)$")
    game_date: date = Field(description="조회 날짜(KST). 오늘부터 5일 안")
    game_time: Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] | None = Field(
        default=None, description="경기 시각 HH:MM. 경기가 없거나 시각을 모르면 비운다")


def default_time(day, now):
    """시각을 모를 때 기준 시각과 그 근거. 오늘은 다음 정시, 다른 날은 18:30."""
    if day == now.date():
        nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if nxt.date() != day:
            return "23:00", "오늘 마지막 정시 예보 기준(경기 시각 아님)"
        return nxt.strftime("%H:%M"), "오늘 다음 정시 기준(경기 시각 아님)"
    return DEFAULT_TIME, f"{DEFAULT_TIME} 기준(경기 시각 아님)"


def create_weather_tools():

    def get_weather(stadium_code, game_date, game_time=None):
        """기존 기상청 서비스로 구장 날씨 단기예보를 조회한다. 시각을 모르면 기준 시각을 정하고 결과에 밝힌다."""
        from travel.weather_service import KST, WeatherError, get_stadium_weather
        basis = None
        if game_time is None:
            game_time, basis = default_time(game_date, datetime.now(KST))
        try:
            result = get_stadium_weather(stadium_code, game_date.isoformat(), game_time)
        except WeatherError as error:
            messages = {
                "invalid_request": "예보 날짜와 구장을 확인해 주세요. 단기예보는 오늘부터 5일 안만 조회된다.",
                "weather_not_configured": "날씨 데이터 연결 설정이 필요합니다.",
                "weather_busy": "날씨 요청이 많아요. 잠시 후 다시 시도해 주세요.",
            }
            raise ToolException(messages.get(error.code, "날씨 정보를 불러오지 못했습니다.")) from None
        if isinstance(result, dict):
            # label 이 "흐림"이면 강수 없음이다. 모델이 "강수 정보 없음"으로 오해하지 않게 명시한다.
            result = {**result, "precipitation": result.get("label") if result.get("label") in PRECIPITATION else "없음"}
        if not basis:
            return result
        if result is None:
            return {"available": False, "requestedTime": game_time, "timeBasis": basis,
                    "message": "기상청 단기예보에 해당 시각 예보가 아직 없다"}
        forecast_at = str(result.get("forecastAt", ""))[11:16]  # 기상청은 정시 예보라 요청 시각과 다를 수 있다
        if forecast_at and forecast_at != game_time:
            basis = f"{basis}, 실제 예보 시각 {forecast_at}"
        return {**result, "requestedTime": game_time, "timeBasis": basis}

    specs = (
        (get_weather, 'get_weather',
         '기상청 단기예보로 구장 날씨를 조회한다(오늘부터 5일 안). 경기가 없거나 경기 시각을 몰라도 날씨를 물으면 호출한다. '
         'game_time은 경기 시각을 알 때만 넣고 모르면 비운다. 비우면 오늘은 다음 정시, 다른 날은 18:30 기준으로 조회하며 '
         '결과의 timeBasis를 답변에 밝힌다.', WeatherInput),
    )
    return tuple(_tool(*spec) for spec in specs)
