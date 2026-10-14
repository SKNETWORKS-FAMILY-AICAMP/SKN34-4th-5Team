from django.db import migrations, models
from django.db.models import F, Sum


ELIGIBLE_TRANSACTION_TYPES = ("ACTIVITY_EARNED", "FANTASY_SETTLEMENT")


def backfill_rank_points(apps, schema_editor):
    PointWallet = apps.get_model("accounts", "PointWallet")
    PointTransaction = apps.get_model("accounts", "PointTransaction")
    database = schema_editor.connection.alias

    PointTransaction.objects.using(database).filter(
        transaction_type__in=ELIGIBLE_TRANSACTION_TYPES,
    ).exclude(source_type="signup").update(rank_delta=F("amount"))
    totals = dict(
        PointTransaction.objects.using(database)
        .values("wallet_id")
        .annotate(total=Sum("rank_delta"))
        .values_list("wallet_id", "total")
    )
    for wallet in PointWallet.objects.using(database).all().iterator():
        wallet.total_earned = max(0, totals.get(wallet.pk) or 0)
        wallet.save(update_fields=("total_earned",), using=database)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_pointwallet_pointtransaction")]

    operations = [
        migrations.AddField(
            model_name="pointwallet",
            name="total_earned",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="pointtransaction",
            name="rank_delta",
            field=models.BigIntegerField(default=0),
        ),
        migrations.RunPython(backfill_rank_points, migrations.RunPython.noop),
    ]
