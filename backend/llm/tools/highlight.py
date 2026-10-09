"""highlight domain tools: collect_highlights 배치가 저장한 하이라이트 장면을 찾아 시작 시각 링크로 돌려준다."""
from datetime import date

from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from pydantic import Field, StrictInt, model_validator

from .common import ToolInput, _json, _result, _tool, db_team_code, is_team_code

# 이벤트를 정하지 않은 "하이라이트" 질문에서 먼저 보여 줄 장면: 타점 1점당 10 + 이벤트 가중치(나머지 5)
EVENT_WEIGHT = {"홈런": 50, "3루타": 30, "2루타": 25, "호수비": 25, "안타": 20, "득점": 20, "실책": 10, "도루": 10}


def importance():
    weight = Case(*(When(event=event, then=Value(score)) for event, score in EVENT_WEIGHT.items()),
                  default=Value(5), output_field=IntegerField())
    return Coalesce("rbi", Value(0)) * 10 + weight


class HighlightInput(ToolInput):
    player_name: str | None = Field(default=None, min_length=2, max_length=40, description="선수 이름(예: 김도영)")
    pitcher_name: str | None = Field(default=None, min_length=2, max_length=40,
                                     description="상대 투수 이름. 'A가 B 상대로 친'이면 player_name=A, pitcher_name=B")
    team_code: str | None = Field(default=None, description="공격 팀 코드(예: KIA, LG, DOOSAN)")
    opponent_team_code: str | None = Field(default=None, description="상대 팀 코드. 'LG전'처럼 상대를 말하면 LG")
    event: str | None = Field(default=None, description="홈런·안타·2루타·3루타·볼넷·삼진·호수비·도루·득점 등. 모르면 비운다")
    date_from: date | None = None
    date_to: date | None = None
    limit: StrictInt | None = Field(default=None, ge=1, le=10,
                                    description="사용자가 여러 개·전부를 달라고 할 때만 넣는다. 비우면 날짜를 특정하지 않은 질문은 가장 최근 1개")

    @model_validator(mode="after")
    def validate_filters(self):
        from llm.highlights import EVENTS
        if any(code and not is_team_code(code) for code in (self.team_code, self.opponent_team_code)):
            raise ValueError("올바른 팀 코드가 아닙니다.")
        if self.event and self.event not in EVENTS:
            raise ValueError(f"event 는 {', '.join(EVENTS)} 중 하나입니다.")
        return self


def pitcher_team(name):
    """투수의 소속 팀 코드: 선수 DB → 그 투수가 나온 저장 장면(수비 팀 = 경기 두 팀 - 공격 팀) 순. 모르면 None.
    분석 모델은 투수 칸을 자주 비우고 설명에만 적어서("고우석이 커브로 양의지를 삼진") 설명도 본다."""
    from collections import Counter
    from baseball.models import Player
    from llm.models import HighlightScene
    codes = set(Player.objects.filter(name=name).values_list("team__team_code", flat=True))
    if len(codes) == 1:
        return codes.pop()
    votes = Counter()
    scenes = HighlightScene.objects.filter(Q(pitcher_name=name) | Q(description__contains=name)).exclude(batter_name=name)
    for scene in scenes.select_related("video__home_team", "video__away_team", "batting_team")[:50]:
        teams = {t.team_code for t in (scene.video.home_team, scene.video.away_team) if t}
        if scene.batting_team and len(teams) == 2:
            votes.update(teams - {scene.batting_team.team_code})
    top = votes.most_common(2)
    return top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None


def create_highlight_tools():
    from llm.highlights import watch_url
    from llm.models import HighlightScene, HighlightVideo

    def search_highlight_scenes(player_name=None, pitcher_name=None, team_code=None, opponent_team_code=None, event=None,
                                date_from=None, date_to=None, limit=None):
        """저장된 KBO 하이라이트 장면을 선수·팀·이벤트·날짜로 찾는다(최신 경기 우선).

        투수 칸은 분석 모델이 자주 비우고, 사용자가 기억하는 날짜는 틀리기도 한다. 그래서 결과가 없으면 투수 → 날짜
        조건 순으로 풀어서 다시 찾고, 푼 조건을 relaxed 에 남겨 답변이 그 사실을 밝히게 한다."""
        base = HighlightScene.objects.filter(video__status=HighlightVideo.ANALYZED).select_related(
            "video__home_team", "video__away_team", "batting_team")
        if player_name:
            # 모델이 타자 칸을 비워도 장면 설명에는 이름을 적는 경우가 있다("나승엽이 … 홈런")
            # 투수로 나온 장면("고우석 상대 홈런")도 찾는다
            base = base.filter(Q(batter_name__icontains=player_name) | Q(batter__name__icontains=player_name)
                               | Q(pitcher_name__icontains=player_name) | Q(description__icontains=player_name))
        if team_code:
            base = base.filter(batting_team__team_code=db_team_code(team_code))
        if opponent_team_code:  # 그 팀이 나온 경기에서, 그 팀이 공격하지 않은 장면
            code = db_team_code(opponent_team_code)
            base = base.filter(Q(video__home_team__team_code=code) | Q(video__away_team__team_code=code))
            base = base.exclude(batting_team__team_code=code)
        if event:
            base = base.filter(event=event)

        defending = pitcher_team(pitcher_name) if pitcher_name else None

        def narrowed(use_pitcher, use_date):
            scenes = base
            if use_pitcher and pitcher_name:
                scenes = scenes.filter(Q(pitcher_name__icontains=pitcher_name) | Q(description__icontains=pitcher_name))
            elif pitcher_name and defending:  # 투수 칸이 비었으면 그 투수 팀이 수비한 장면으로만 좁힌다
                scenes = scenes.filter(Q(video__home_team__team_code=defending) | Q(video__away_team__team_code=defending))
                scenes = scenes.exclude(batting_team__team_code=defending).exclude(batting_team__isnull=True)
            if use_date and date_from:
                scenes = scenes.filter(video__game_date__gte=date_from)
            if use_date and date_to:
                scenes = scenes.filter(video__game_date__lte=date_to)
            return scenes

        has_date = bool(date_from or date_to)
        # 투수만으로 장면을 특정한 질문에서 투수 팀도 모르면 투수 조건을 풀지 않는다(그날 다른 경기 홈런이 섞임)
        can_drop_pitcher = bool(pitcher_name) and bool(defending or player_name or team_code or opponent_team_code)
        attempts = [(True, True)]
        if can_drop_pitcher:
            attempts.append((False, True))
        if has_date:
            attempts.append((True, False))
        if can_drop_pitcher and has_date:
            attempts.append((False, False))
        scenes, relaxed = narrowed(True, True), []
        for use_pitcher, use_date in attempts:
            candidate = narrowed(use_pitcher, use_date)
            if candidate.exists():
                scenes = candidate
                relaxed = [name for name, dropped in (("pitcher", pitcher_name and not use_pitcher),
                                                      ("date", has_date and not use_date)) if dropped]
                break
        # "10월 6일 하이라이트"처럼 날짜만 말한 질문은 그날 경기마다 대표 장면 하나씩
        per_game = has_date and "date" not in relaxed and not any(
            (player_name, pitcher_name, team_code, opponent_team_code, event))
        if limit is None:  # 날짜를 특정한 질문은 그날 장면(최대 3개), 아니면 가장 최근 장면 하나
            limit = 10 if per_game else 3 if has_date and "date" not in relaxed else 1
        if event:
            scenes = scenes.order_by("-video__game_date", "-video__published_at", "start_sec")
        else:  # 이벤트를 말하지 않았으면 같은 날 안에서 득점·홈런 같은 주요 장면부터 고른다
            scenes = scenes.annotate(score=importance()).order_by(
                "-video__game_date", "-score", "-video__published_at", "start_sec")
        if per_game:
            best = {}
            for scene in scenes:
                best.setdefault(scene.video_id, scene)
            ordered = list(best.values())
        else:
            ordered = list(scenes[:max(limit, 50)])
        requested_date = None
        if "date" in relaxed:  # 날짜를 잘못 기억한 질문이면 요청한 날짜에 가장 가까운 장면 하나만 바로잡아 보여 준다
            target = requested_date = date_from or date_to
            ordered.sort(key=lambda s: abs((s.video.game_date - target).days) if s.video.game_date else 9999)
            limit = 1
        ordered = ordered[:limit]
        if not event:  # 고른 장면은 경기 흐름대로 보여 준다
            ordered.sort(key=lambda s: (-(s.video.game_date or date.min).toordinal(), s.video_id, s.start_sec))
        items = [{
            "video_id": s.video_id, "title": s.video.title, "game_date": _json(s.video.game_date),
            "matchup": " vs ".join(t.team_code for t in (s.video.away_team, s.video.home_team) if t),
            "inning": s.inning, "half": s.half or None,
            "batting_team": s.batting_team.team_code if s.batting_team else None,
            "batter": s.batter_name or None, "pitcher": s.pitcher_name or None, "event": s.event, "rbi": s.rbi, "description": s.description,
            "start_sec": s.start_sec, "url": watch_url(s.video_id, s.start_sec),
        } for s in ordered]
        analyzed = HighlightVideo.objects.filter(status=HighlightVideo.ANALYZED)
        extra = {"note": "조건을 풀어 찾은 장면이라 공식 기록과 다를 수 있다."} if relaxed else {}
        return _result(items, relaxed=relaxed, requested_date=_json(requested_date), analyzed_videos=analyzed.count(),
                       latest_game_date=_json(analyzed.order_by("-game_date").values_list("game_date", flat=True).first()),
                       **extra)

    specs = (
        (search_highlight_scenes, "search_highlight_scenes",
         "KBO 경기 하이라이트 영상에서 선수·팀·이벤트(홈런 등) 장면을 찾아 그 장면부터 재생되는 유튜브 링크를 돌려준다.",
         HighlightInput),
    )
    return tuple(_tool(*spec) for spec in specs)
