from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PointRankMigrationTest(TransactionTestCase):
    migrate_from = ("accounts", "0008_pointwallet_pointtransaction")
    migrate_to = ("accounts", "0009_point_rank")

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        User = old_apps.get_model("accounts", "CustomUser")
        Wallet = old_apps.get_model("accounts", "PointWallet")
        Transaction = old_apps.get_model("accounts", "PointTransaction")
        User.objects.filter(username="rank-migration-user").delete()
        user = User.objects.create(username="rank-migration-user", password="!")
        wallet = Wallet.objects.create(user=user, balance=70)
        for amount, transaction_type, source in (
            (100, "ACTIVITY_EARNED", "signup"),
            (100, "ACTIVITY_EARNED", "reward"),
            (25, "FANTASY_SETTLEMENT", "fantasy"),
            (-10, "ACTIVITY_EARNED", "reversal"),
            (-45, "SPEND", "spend"),
            (20, "ADMIN_ADJUSTMENT", "admin"),
        ):
            Transaction.objects.create(
                user=user, wallet=wallet, amount=amount,
                transaction_type=transaction_type, source_type=source,
                source_key=source,
            )

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )

    def test_backfills_only_eligible_reward_ledger_deltas(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        Wallet = apps.get_model("accounts", "PointWallet")
        Transaction = apps.get_model("accounts", "PointTransaction")
        wallet = Wallet.objects.get(user__username="rank-migration-user")
        self.assertEqual(wallet.total_earned, 115)
        deltas = dict(Transaction.objects.values_list("source_key", "rank_delta"))
        self.assertEqual(deltas, {
            "signup": 0, "reward": 100, "fantasy": 25, "reversal": -10, "spend": 0, "admin": 0,
        })
