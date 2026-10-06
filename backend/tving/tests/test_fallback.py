from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory
from baseball.models import PlayerSeasonRecord, ScheduleDay, StandingHistory, Team
from tving.relational import TEAM_MAP, persist_daily, read_daily, read_month
from tving.service import refresh_daily
from tving.views import DailyView
from tving.tests.test_tving import relational_daily


class FallbackTests(TestCase):
    def setUp(self):
        for index, code in enumerate(TEAM_MAP.values(), 1):
            Team.objects.update_or_create(team_code=code, defaults={"id": index, "team_name_ko": code})
        self.stamp = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)

    def snapshot(self, day):
        persist_daily(relational_daily(day), self.stamp)

    def test_latest_complete_same_season_snapshot_and_real_timestamp(self):
        self.snapshot("2026-09-28")
        self.snapshot("2026-09-29")
        StandingHistory.objects.filter(snapshot_date="2026-09-29", rank=10).delete()
        data = read_daily("2026-09-30")
        self.assertEqual(len(data["standings"]), 10)
        self.assertEqual(data["standingsMeta"], {"status": "ready", "date": "2026-09-28",
                         "updatedAt": self.stamp.isoformat(), "isFallback": True})
        self.assertEqual(data["scheduleStatus"], "pending")

    def test_current_snapshot_automatically_replaces_fallback(self):
        self.snapshot("2026-09-29")
        self.assertTrue(read_daily("2026-09-30")["standingsMeta"]["isFallback"])
        self.snapshot("2026-09-30")
        self.assertFalse(read_daily("2026-09-30")["standingsMeta"]["isFallback"])

    def test_inconsistent_current_totals_fall_back(self):
        self.snapshot("2026-09-28")
        self.snapshot("2026-09-29")
        StandingHistory.objects.filter(snapshot_date="2026-09-29", rank=1).update(played=99)
        self.assertEqual(read_daily("2026-09-29")["standingsMeta"]["date"], "2026-09-28")

    def test_schedule_error_is_not_empty(self):
        self.snapshot("2026-09-29")
        ScheduleDay.objects.filter(date="2026-09-29").update(status="error")
        self.assertEqual(read_daily("2026-09-29")["scheduleStatus"], "error")
        self.assertEqual(len(read_daily("2026-09-29")["standings"]), 10)

    def test_month_keeps_complete_games_while_other_days_are_missing(self):
        payload = relational_daily("2026-09-29")
        payload["games"] = [{
            "id": "20260929SSKT0", "date": "2026-09-29", "startsAt": "2026-09-29T18:30:00+09:00",
            "time": "18:30", "stadium": "수원", "status": "scheduled", "statusLabel": "경기 예정",
            "away": {"code": "SS", "name": "삼성", "score": None},
            "home": {"code": "KT", "name": "KT", "score": None},
        }]
        persist_daily(payload, self.stamp)
        data = read_month("2026-09", "2026-09-29")
        self.assertEqual(data["days"][28]["status"], "ready")
        self.assertEqual(data["days"][29]["status"], "pending")
        self.assertEqual([game["id"] for game in data["games"]], ["20260929SSKT0"])

    def test_no_previous_season_or_future_fallback(self):
        self.snapshot("2025-12-31")
        self.snapshot("2026-10-02")
        self.assertEqual(read_daily("2026-10-01")["standings"], [])

    def test_no_mixed_dates_or_incomplete_rows(self):
        self.snapshot("2026-09-28")
        self.snapshot("2026-09-29")
        StandingHistory.objects.filter(snapshot_date="2026-09-28", rank__gt=5).delete()
        StandingHistory.objects.filter(snapshot_date="2026-09-29", rank__lte=5).delete()
        self.assertEqual(read_daily("2026-09-30")["standings"], [])

    def test_independent_rankings_and_confirmed_empty_schedule(self):
        self.snapshot("2026-09-29")
        PlayerSeasonRecord.objects.filter(record_kind="hitter").delete()
        data = read_daily("2026-09-29")
        self.assertEqual(data["scheduleStatus"], "empty")
        self.assertEqual(len(data["standings"]), 10)
        self.assertTrue(data["individualRankings"]["pitchers"])
        self.assertEqual(data["individualRankingsStatus"]["hitters"], "pending")

    def test_incomplete_schedule_is_not_empty(self):
        self.snapshot("2026-09-29")
        ScheduleDay.objects.filter(date="2026-09-29").update(status="ready", game_count=1, game_codes=["missing-game"])
        self.assertEqual(read_daily("2026-09-29")["scheduleStatus"], "pending")

    def test_month_returns_all_days_even_when_only_one_was_collected(self):
        self.snapshot("2026-09-29")
        data = read_month("2026-09", "2026-09-29")
        self.assertEqual(len(data["days"]), 30)
        self.assertEqual(data["days"][28]["status"], "empty")
        self.assertEqual(data["days"][29]["status"], "pending")
        self.assertFalse(data["loading"])

    def test_no_data_response_has_null_timestamp_not_fabricated_now(self):
        data = refresh_daily("2026-09-30")
        self.assertEqual(data["scheduleStatus"], "pending")
        self.assertIsNone(data["fetchedAt"])
        self.assertIsNone(data["nextCheckAt"])

    @patch("tving.views._run_local_crawler_if_needed")
    def test_database_error_remains_http_error(self, crawler):
        with patch("tving.views.DailyView.refresh", side_effect=RuntimeError("database unavailable")):
            response = DailyView.as_view()(APIRequestFactory().get("/", {"date": "2026-09-30"}))
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(response.data["data"])

    @patch("tving.views._run_local_crawler_if_needed", side_effect=RuntimeError("collector unavailable"))
    def test_collector_error_does_not_hide_persisted_data(self, crawler):
        self.snapshot("2026-09-29")
        response = DailyView.as_view()(APIRequestFactory().get("/", {"date": "2026-09-30"}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["standingsMeta"]["isFallback"])
