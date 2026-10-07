from baseball.models import Game


CANCELLED_GAME_STATUSES = {"cancel", "cancelled", "canceled", "취소"}


def scheduled_games(week):
    return scheduled_games_on(week_start=week.week_start, week_end=week.week_end)


def scheduled_games_on(*, game_date=None, week_start=None, week_end=None):
    games = Game.objects.all()
    if game_date is not None:
        games = games.filter(game_date=game_date)
    else:
        games = games.filter(game_date__gte=week_start, game_date__lte=week_end)
    for cancelled_status in CANCELLED_GAME_STATUSES:
        games = games.exclude(status_code__iexact=cancelled_status)
    return games


def week_has_games(week):
    return scheduled_games(week).filter(
        home_team__isnull=False,
        away_team__isnull=False,
    ).exists()
