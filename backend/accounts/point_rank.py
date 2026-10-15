from .models import PointWallet
from .point_policy import MEMBER_RANK_THRESHOLDS


def rank_for_points(points):
    for rank, minimum in reversed(tuple(enumerate(MEMBER_RANK_THRESHOLDS, start=2))):
        if points >= minimum:
            return rank
    return 1


def member_rank(member):
    try:
        points = member.point_wallet.total_earned
    except PointWallet.DoesNotExist:
        points = 0
    return rank_for_points(points)
