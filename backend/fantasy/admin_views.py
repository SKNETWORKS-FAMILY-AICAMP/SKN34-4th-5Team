from datetime import date, timedelta

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from accounts.models import PointTransaction
from baseball.models import Game
from fantasy.models import FantasyBattingGameStat, FantasyPitchingGameStat, FantasySettlement, FantasyWeek
from fantasy.services.schedule import scheduled_games, scheduled_games_on
from fantasy.services.settlement import (
    TEST_SETTLEMENT_SOURCE_TYPE,
    FantasySettlementNotReady,
    FantasyTestSettlementConflict,
    FantasyTestSettlementNotFound,
    cancel_test_settlement,
    settle_week_for_testing,
)
from fantasy.services.stat_import import StatImportError, parse_stat_table
from fantasy.services.weeks import current_week, fantasy_today, week_bounds


class FantasyAdminTestSettlementView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        week = current_week()
        settlement = FantasySettlement.objects.filter(week=week).first()
        is_test_settlement = False
        if settlement:
            first_result = settlement.results.order_by("pk").first()
            is_test_settlement = bool(first_result) and PointTransaction.objects.filter(
                user_id=first_result.user_id,
                source_type=TEST_SETTLEMENT_SOURCE_TYPE,
                source_key=f"{settlement.pk}:{first_result.user_id}",
            ).exists()
        return Response({
            "week_start": week.week_start,
            "week_end": week.week_end,
            "status": week.status,
            "is_test_settlement": is_test_settlement,
        })

    @transaction.atomic
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        week = current_week()
        try:
            settlement = settle_week_for_testing(week.pk)
        except FantasySettlementNotReady as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except FantasyTestSettlementConflict as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)

        results = list(settlement.results.all())
        return Response({
            "detail": "현재 점수 기준으로 테스트 포인트를 지급했습니다.",
            "week_start": week.week_start,
            "week_end": week.week_end,
            "status": FantasyWeek.SETTLED,
            "is_test_settlement": True,
            "user_count": len(results),
            "total_points": sum(result.point_amount for result in results),
        })

    @transaction.atomic
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def delete(self, request):
        week = current_week()
        try:
            user_count, reversed_points = cancel_test_settlement(week.pk)
        except FantasyTestSettlementNotFound as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        except FantasyTestSettlementConflict as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)

        return Response({
            "detail": "테스트 포인트를 회수하고 주차를 미결산으로 되돌렸습니다.",
            "week_start": week.week_start,
            "week_end": week.week_end,
            "status": FantasyWeek.OPEN,
            "is_test_settlement": False,
            "user_count": user_count,
            "reversed_points": reversed_points,
        })


class FantasyAdminContextView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        week = current_week()
        return Response({
            "week_start": week.week_start,
            "week_end": week.week_end,
            "today": fantasy_today(),
        })


class FantasyAdminGameListView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        try:
            game_date = date.fromisoformat(request.query_params["date"])
        except (KeyError, ValueError):
            return Response(
                {"detail": "유효한 경기 날짜를 지정해 주세요. (YYYY-MM-DD)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        today = fantasy_today()
        if game_date > today:
            return Response(
                {"detail": "미래 날짜의 경기 기록은 입력할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        week_start, week_end = week_bounds(game_date)
        week = FantasyWeek.objects.filter(week_start=week_start, week_end=week_end).first()
        if week and week.status == FantasyWeek.SETTLED:
            return Response(
                {"detail": "이미 결산된 주차의 기록은 변경할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        games = scheduled_games_on(game_date=game_date).filter(
            home_team__isnull=False, away_team__isnull=False
        ).select_related(
            "home_team", "away_team"
        ).order_by("game_time", "id")
        return Response([
            {
                "id": game.pk,
                "game_code": game.game_code,
                "game_date": game.game_date,
                "game_time": game.game_time,
                "home_team": {
                    "code": game.home_team.team_code,
                    "name": game.home_team.team_name_ko,
                } if game.home_team else None,
                "away_team": {
                    "code": game.away_team.team_code,
                    "name": game.away_team.team_name_ko,
                } if game.away_team else None,
            }
            for game in games
        ])


class FantasyAdminStatImportView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    @transaction.atomic
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def delete(self, request):
        game_id = request.query_params.get("game_id")
        if not str(game_id or "").isdigit():
            return Response(
                {"detail": "경기 일정을 선택해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        game = Game.objects.select_for_update(of=("self",)).filter(pk=int(game_id)).first()
        if game is None:
            return Response({"detail": "선택한 경기를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        if game.game_date > fantasy_today() or not scheduled_games_on(game_date=game.game_date).filter(pk=game.pk).exists():
            return Response(
                {"detail": "선택한 경기의 기록을 삭제할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        week_start, week_end = week_bounds(game.game_date)
        week = FantasyWeek.objects.filter(week_start=week_start, week_end=week_end).first()
        if week:
            week = FantasyWeek.objects.select_for_update().get(pk=week.pk)
            if week.status == FantasyWeek.SETTLED:
                return Response(
                    {"detail": "이미 결산된 주차의 기록은 삭제할 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        batting_count, _ = FantasyBattingGameStat.objects.filter(game=game).delete()
        pitching_count, _ = FantasyPitchingGameStat.objects.filter(game=game).delete()
        if week and week.stats_finalized_at is not None:
            week.stats_finalized_at = None
            week.save(update_fields=("stats_finalized_at", "updated_at"))

        return Response({
            "detail": "선택 경기의 환상게임 기록을 삭제했습니다.",
            "game_id": game.pk,
            "batting_count": batting_count,
            "pitching_count": pitching_count,
        })

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        game_id = request.query_params.get("game_id")
        if not str(game_id or "").isdigit():
            return Response(
                {"detail": "경기 일정을 선택해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        game = Game.objects.select_related("home_team", "away_team").filter(pk=int(game_id)).first()
        if game is None:
            return Response({"detail": "선택한 경기를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        if game.game_date > fantasy_today() or not scheduled_games_on(game_date=game.game_date).filter(pk=game.pk).exists():
            return Response(
                {"detail": "선택한 경기의 기록을 조회할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        week_start, week_end = week_bounds(game.game_date)
        week = FantasyWeek.objects.filter(week_start=week_start, week_end=week_end).first()
        if week and week.status == FantasyWeek.SETTLED:
            return Response(
                {"detail": "이미 결산된 주차의 기록은 조회할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        batting = FantasyBattingGameStat.objects.filter(game=game).select_related(
            "player", "team"
        ).order_by("team__team_name_ko", "player__name")
        pitching = FantasyPitchingGameStat.objects.filter(game=game).select_related(
            "player", "team"
        ).order_by("team__team_name_ko", "player__name")
        return Response({
            "game_id": game.pk,
            "batting": [
                {
                    "player_name": row.player.name,
                    "team_name": row.team.team_name_ko,
                    "at_bats": row.at_bats,
                    "hits": row.hits,
                    "rbi": row.rbi,
                    "runs": row.runs,
                }
                for row in batting
            ],
            "pitching": [
                {
                    "player_name": row.player.name,
                    "team_name": row.team.team_name_ko,
                    "saves": row.saves,
                    "batters_faced": row.batters_faced,
                    "strikeouts": row.strikeouts,
                    "pitch_count": row.pitch_count,
                }
                for row in pitching
            ],
        })

    @transaction.atomic
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        game_id = request.data.get("game_id")
        if not str(game_id or "").isdigit():
            return Response(
                {"detail": "경기 일정을 선택해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        game = Game.objects.select_for_update(of=("self",)).select_related(
            "home_team", "away_team"
        ).filter(pk=int(game_id)).first()
        if game is None:
            return Response({"detail": "선택한 경기를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        if not game.home_team_id or not game.away_team_id:
            return Response(
                {"detail": "선택한 경기의 양 팀 정보가 없어 기록을 연결할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        today = fantasy_today()
        if game.game_date > today:
            return Response(
                {"detail": "미래 날짜의 경기 기록은 입력할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        week_start, week_end = week_bounds(game.game_date)
        if not scheduled_games_on(game_date=game.game_date).filter(pk=game.pk).exists():
            return Response(
                {"detail": "취소되었거나 입력할 수 없는 일정입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        week, _ = FantasyWeek.objects.get_or_create(
            week_start=week_start,
            week_end=week_end,
            defaults={"settlement_date": week_end + timedelta(days=2)},
        )
        week = FantasyWeek.objects.select_for_update().get(pk=week.pk)
        if week.status == FantasyWeek.SETTLED:
            return Response(
                {"detail": "이미 결산된 주차의 기록은 변경할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        batting_text = request.data.get("batting")
        pitching_text = request.data.get("pitching")
        if not isinstance(batting_text, str) or not isinstance(pitching_text, str):
            return Response(
                {"detail": "타자 기록과 투수 기록을 모두 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        errors = []
        batting_rows = []
        pitching_rows = []
        for kind, text, target in (
            ("batting", batting_text, batting_rows),
            ("pitching", pitching_text, pitching_rows),
        ):
            try:
                target.extend(parse_stat_table(text, kind, game))
            except StatImportError as error:
                errors.extend(error.errors)

        for kind, rows in (("타자", batting_rows), ("투수", pitching_rows)):
            if rows:
                included_teams = {row["team"].pk for row in rows}
                game_teams = {team.pk for team in (game.home_team, game.away_team) if team}
                if included_teams != game_teams:
                    errors.append(f"{kind} 기록에 선택 경기 양쪽 구단의 선수 기록을 모두 입력해 주세요.")
        if errors:
            return Response(
                {"detail": "입력한 기록을 저장하지 않았습니다.", "errors": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        FantasyBattingGameStat.objects.filter(game=game).delete()
        FantasyPitchingGameStat.objects.filter(game=game).delete()
        FantasyBattingGameStat.objects.bulk_create([
            FantasyBattingGameStat(
                game=game,
                player=row["player"],
                team=row["team"],
                at_bats=row["at_bats"],
                hits=row["hits"],
                rbi=row["rbi"],
                runs=row["runs"],
            )
            for row in batting_rows
        ])
        FantasyPitchingGameStat.objects.bulk_create([
            FantasyPitchingGameStat(
                game=game,
                player=row["player"],
                team=row["team"],
                saves=row["saves"],
                batters_faced=row["batters_faced"],
                strikeouts=row["strikeouts"],
                pitch_count=row["pitch_count"],
            )
            for row in pitching_rows
        ])

        if week.stats_finalized_at is not None:
            week.stats_finalized_at = None
            week.save(update_fields=("stats_finalized_at", "updated_at"))

        return Response({
            "detail": "경기 선수 기록을 저장했습니다.",
            "game_id": game.pk,
            "batting_count": len(batting_rows),
            "pitching_count": len(pitching_rows),
            "stats_finalized": False,
        })
