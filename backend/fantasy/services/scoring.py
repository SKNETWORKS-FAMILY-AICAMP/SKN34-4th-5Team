from decimal import Decimal

from django.db.models import Sum

from fantasy.models import FantasyBattingGameStat, FantasyPitchingGameStat

BASE_SCORE = {
    "at_bats": Decimal("1"), "hits": Decimal("4"), "rbi": Decimal("5"), "runs": Decimal("5"),
    "saves": Decimal("30"), "batters_faced": Decimal("1"), "strikeouts": Decimal("3"), "pitch_count": Decimal("0.1"),
}
WEIGHT_MIN, WEIGHT_MAX, NEUTRAL_WEIGHT = 1, 9, 5
BATTER_STATS = ("at_bats", "hits", "rbi", "runs")
PITCHER_STATS = ("saves", "batters_faced", "strikeouts", "pitch_count")
PITCHER_POSITION_NAMES = {"pitcher", "투수"}


def player_fantasy_type(player):
    positions = {str(value).lower() for value in (player.positions or [])}
    return "PITCHER" if positions & PITCHER_POSITION_NAMES else "BATTER"


def _score_values(selection):
    week = selection.week
    games = {"game__game_date__gte": week.week_start, "game__game_date__lte": week.week_end, "player": selection.player}
    if player_fantasy_type(selection.player) == "PITCHER":
        rows = FantasyPitchingGameStat.objects.filter(**games).aggregate(
            saves=Sum("saves"),
            batters_faced=Sum("batters_faced"),
            strikeouts=Sum("strikeouts"),
            pitch_count=Sum("pitch_count"),
        )
        return rows
    return FantasyBattingGameStat.objects.filter(**games).aggregate(**{key: Sum(key) for key in BATTER_STATS})


def score_selection(selection):
    if not selection.is_confirmed:
        return Decimal("0")
    values = _score_values(selection)
    stats = PITCHER_STATS if player_fantasy_type(selection.player) == "PITCHER" else BATTER_STATS
    return sum(
        Decimal(values.get(stat) or 0) * BASE_SCORE[stat] * Decimal(selection.stat_weights[stat]) / NEUTRAL_WEIGHT
        for stat in stats
    )


def score_user_week(user, week):
    return sum((score_selection(selection) for selection in week.selections.filter(user=user)), Decimal("0"))
