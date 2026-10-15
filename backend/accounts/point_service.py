from django.db import transaction

from accounts.models import CustomUser, PointTransaction, PointWallet


@transaction.atomic
def grant_points(
    *, user=None, user_id=None, amount, source_type, source_key, description="", counts_for_rank=True,
):
    """멱등적으로 포인트를 지급하고 잔액과 원장을 함께 갱신한다."""
    if type(amount) is not int or amount < 0:
        raise ValueError("지급 포인트는 음수일 수 없습니다.")
    return adjust_points(
        user=user, user_id=user_id, amount=amount, rank_delta=amount if counts_for_rank else 0,
        transaction_type=PointTransaction.FANTASY_SETTLEMENT
        if source_type.startswith("fantasy.") else PointTransaction.ACTIVITY_EARNED,
        source_type=source_type, source_key=source_key, description=description,
    )


@transaction.atomic
def adjust_points(
    *, user=None, user_id=None, amount, transaction_type, source_type, source_key,
    rank_delta=0, description="",
):
    """잔액과 등급 누적값을 원장에 명시된 증감만큼 원자적으로 조정한다."""
    if type(amount) is not int:
        raise ValueError("포인트 조정액은 정수여야 합니다.")
    if type(rank_delta) is not int:
        raise ValueError("등급 반영액은 정수여야 합니다.")
    if user is None:
        user = CustomUser.objects.get(pk=user_id)
    wallet, _ = PointWallet.objects.select_for_update().get_or_create(user=user)
    existing = PointTransaction.objects.filter(user=user, source_type=source_type, source_key=source_key).first()
    if existing:
        return wallet, existing, False
    if wallet.balance + amount < 0:
        raise ValueError("포인트 잔액보다 크게 차감할 수 없습니다.")
    if wallet.total_earned + rank_delta < 0:
        raise ValueError("누적 등급 포인트는 음수일 수 없습니다.")
    wallet.balance += amount
    wallet.total_earned += rank_delta
    wallet.save(update_fields=("balance", "total_earned", "updated_at"))
    entry = PointTransaction.objects.create(
        user=user, wallet=wallet, amount=amount, rank_delta=rank_delta,
        transaction_type=transaction_type,
        source_type=source_type, source_key=source_key, description=description,
    )
    return wallet, entry, True
