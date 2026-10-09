"""하이라이트 장면 수집·검색 테스트 (YouTube·OpenRouter 호출은 가짜 응답)."""
import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from baseball.models import Player, Team
from llm import highlights
from llm.models import HighlightScene, HighlightVideo
from llm.tools.highlight import create_highlight_tools

TITLE = "[LG트윈스 vs KIA타이거즈] 10.6(화) 야구 하이라이트｜2026 KBO 리그｜KBO X TVING"


class ParseTest(TestCase):
    def test_title_teams_and_date(self):
        self.assertEqual(highlights.parse_title(TITLE), ("LG", "KIA", date(2026, 10, 6)))
        self.assertEqual(highlights.parse_title("[삼성라이온즈 vs 키움히어로즈] 9.30(화) 야구 하이라이트｜2026 KBO 리그"),
                         ("SAMSUNG", "KIWOOM", date(2026, 9, 30)))
        self.assertTrue(highlights.is_highlight(TITLE))
        self.assertFalse(highlights.is_highlight("중요한 순간 역전을 만든 LG트윈스! | 두산 vs LG | 10.7 | 크보모먼트"))

    def test_time_helpers(self):
        self.assertEqual(highlights.seconds("03:07"), 187)
        self.assertEqual(highlights.seconds("1:02:03"), 3723)
        self.assertIsNone(highlights.seconds("3분"))
        self.assertEqual(highlights.iso_duration("PT14M35S"), 875)
        self.assertEqual(highlights.watch_url("abc", 187), "https://www.youtube.com/watch?v=abc&t=184s")
        self.assertEqual(highlights.watch_url("abc", 1), "https://www.youtube.com/watch?v=abc&t=0s")


def fake_http(scenes, cost=0.12):
    calls = []

    def http(url, body=None, headers=None, timeout=60):
        calls.append(url)
        if "playlistItems" in url:
            return {"items": [
                {"snippet": {"title": TITLE}, "contentDetails": {"videoId": "vid1", "videoPublishedAt": "2026-10-06T14:00:00Z"}},
                {"snippet": {"title": "크보모먼트 | 두산 vs LG"}, "contentDetails": {"videoId": "skip", "videoPublishedAt": "2026-10-06T13:00:00Z"}},
            ]}
        if "videos?" in url:
            return {"items": [{"id": "vid1", "contentDetails": {"duration": "PT10M0S"}}]}
        assert body["provider"] == {"only": ["google-ai-studio"], "allow_fallbacks": False}
        assert body["messages"][0]["content"][1]["video_url"]["url"] == "https://www.youtube.com/watch?v=vid1"
        return {"choices": [{"message": {"content": json.dumps({"scenes": scenes})}}], "usage": {"cost": cost},
                "provider": "Google AI Studio"}
    http.calls = calls
    return http


SCENES = [
    {"start": "03:07", "end": "03:30", "inning": 3, "half": "말", "batting_team": "KIA", "batter": "김도영",
     "pitcher": "임찬규", "event": "홈런", "rbi": 2, "description": "김도영이 좌월 투런 홈런을 친다."},
    {"start": "05:00", "end": "05:10", "inning": 5, "half": "초", "batting_team": "LG트윈스", "batter": None,
     "pitcher": None, "event": "삼진", "rbi": None, "description": "삼진."},
    {"start": "99:00", "end": "99:10", "inning": 9, "half": "말", "batting_team": "KIA", "batter": "누군가",
     "pitcher": None, "event": "홈런", "rbi": 1, "description": "영상 길이 밖이라 버린다."},
    {"start": "06:00", "end": "06:05", "inning": 6, "half": "초", "batting_team": "KIA", "batter": "x",
     "pitcher": None, "event": "끝내기", "rbi": None, "description": "목록에 없는 이벤트라 버린다."},
]


@patch.dict("os.environ", {"YOUTUBE_API_KEY": "yt", "OPENROUTER_API_KEY": "or"})
@patch("llm.highlights.roster_names", return_value={"LG": [], "KIA": ["김도영"]})
class CollectTest(TestCase):
    def setUp(self):
        self.kia, _ = Team.objects.get_or_create(team_code="KIA", defaults={"id": 990001, "team_name_ko": "KIA 타이거즈"})
        self.lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        Player.objects.update_or_create(external_code="test-kdy", defaults={"team": self.kia, "name": "김도영"})

    def test_dry_run_saves_nothing(self, _roster):
        http = fake_http(SCENES)
        summary = highlights.collect(limit=5, dry_run=True, report=lambda _: None, _http=http)
        self.assertEqual(summary["found"], 1)  # 크보모먼트는 제외
        self.assertFalse(HighlightVideo.objects.exists())
        self.assertFalse(any("openrouter" in url for url in http.calls))

    def test_collect_validates_and_skips_analyzed(self, _roster):
        summary = highlights.collect(limit=5, budget=1.0, report=lambda _: None, _http=fake_http(SCENES))
        self.assertEqual((summary["analyzed"], summary["cost"]), (1, 0.12))
        video = HighlightVideo.objects.get(video_id="vid1")
        self.assertEqual((video.status, video.duration_sec, video.home_team, video.away_team), ("analyzed", 600, self.kia, self.lg))
        scenes = list(video.scenes.order_by("start_sec"))
        self.assertEqual([s.event for s in scenes], ["홈런", "삼진"])  # 길이 밖·목록 밖 이벤트는 버림
        homer, strikeout = scenes
        self.assertEqual((homer.start_sec, homer.batter.external_code, homer.batting_team, homer.rbi), (187, "test-kdy", self.kia, 2))
        self.assertEqual(strikeout.batting_team, self.lg)  # 'LG트윈스' 표기도 팀 코드로
        again = highlights.collect(limit=5, report=lambda _: None, _http=fake_http(SCENES))
        self.assertEqual((again["analyzed"], again["skipped"]), (0, 1))

    def test_empty_batter_is_filled_from_description(self, _roster):
        scenes = highlights.clean_scenes([
            {"start": "01:00", "event": "홈런", "batter": None, "description": "나승엽이 우측 폴대를 맞히는 솔로 홈런을 터뜨린다."},
            {"start": "02:00", "event": "투수 교체", "batter": None, "description": "고우석이 마운드에 오른다."},
        ], 600, ["KIA", "LOTTE"])
        self.assertEqual([s["batter_name"] for s in scenes], ["나승엽", ""])

    def test_pitcher_carries_within_half_inning_until_change(self, _roster):
        scenes = highlights.clean_scenes([
            {"start": "01:00", "inning": 9, "half": "초", "event": "안타", "batter": "박정우", "pitcher": "고우석", "description": "안타."},
            {"start": "01:20", "inning": 9, "half": "초", "event": "안타", "batter": "박재현", "pitcher": None, "description": "안타."},
            {"start": "01:40", "inning": 9, "half": "초", "event": "홈런", "batter": "김도영", "pitcher": None, "description": "역전 홈런."},
            {"start": "02:00", "inning": 9, "half": "초", "event": "투수 교체", "batter": None, "pitcher": None, "description": "김진성이 마운드에 오른다."},
            {"start": "02:10", "inning": 9, "half": "초", "event": "삼진", "batter": "최형우", "pitcher": None, "description": "삼진."},
            {"start": "03:00", "inning": 9, "half": "말", "event": "아웃", "batter": "문성주", "pitcher": None, "description": "아웃."},
        ], 600, ["KIA", "LG"])
        self.assertEqual([s["pitcher_name"] for s in scenes], ["고우석", "고우석", "고우석", "김진성", "김진성", ""])

    def test_bad_model_output_marks_failed(self, _roster):
        http = fake_http(SCENES)

        def broken(url, body=None, headers=None, timeout=60):
            if "openrouter" in url:
                return {"choices": [{"message": {"content": "죄송합니다"}}]}
            return http(url, body, headers, timeout)
        summary = highlights.collect(limit=5, report=lambda _: None, _http=broken)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(HighlightVideo.objects.get(video_id="vid1").status, "failed")

    def test_network_error_or_error_payload_fails_only_that_video(self, _roster):
        http = fake_http(SCENES)
        for failure in (TimeoutError("read timed out"), {"error": {"message": "provider down"}}):
            def flaky(url, body=None, headers=None, timeout=60, failure=failure):
                if "openrouter" not in url:
                    return http(url, body, headers, timeout)
                if isinstance(failure, Exception):
                    raise failure
                return failure
            summary = highlights.collect(limit=5, report=lambda _: None, _http=flaky)
            self.assertEqual(summary["failed"], 1)
            self.assertIn("OpenRouter", HighlightVideo.objects.get(video_id="vid1").error)


class SearchToolTest(TestCase):
    def setUp(self):
        kia, _ = Team.objects.get_or_create(team_code="KIA", defaults={"id": 990001, "team_name_ko": "KIA 타이거즈"})
        video = HighlightVideo.objects.create(video_id="vid1", title=TITLE, game_date=date(2026, 10, 6), status="analyzed")
        HighlightScene.objects.create(video=video, start_sec=187, inning=3, half="말", batting_team=kia,
                                      batter_name="김도영", event="홈런", rbi=2, description="좌월 투런 홈런")
        HighlightScene.objects.create(video=video, start_sec=300, batting_team=kia, batter_name="김도영", event="안타")
        HighlightScene.objects.create(video=video, start_sec=420, batting_team=kia, event="홈런", description="나성범이 솔로 홈런을 친다.")
        HighlightVideo.objects.create(video_id="vid2", title=TITLE, status="failed")
        self.tool = create_highlight_tools()[0]

    def test_finds_player_homer_with_start_link(self):
        result = self.tool.invoke({"player_name": "김도영", "event": "홈런"})
        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["url"], "https://www.youtube.com/watch?v=vid1&t=184s")
        self.assertEqual((item["inning"], item["half"], item["batting_team"]), (3, "말", "KIA"))
        self.assertEqual((result["analyzed_videos"], result["latest_game_date"]), (1, "2026-10-06"))
        by_description = self.tool.invoke({"player_name": "나성범", "event": "홈런"})  # 타자 칸이 비어도 설명으로 찾는다
        self.assertEqual([i["start_sec"] for i in by_description["items"]], [420])

    def test_opponent_and_pitcher_filters(self):
        lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        kia = Team.objects.get(team_code="KIA")
        video = HighlightVideo.objects.create(video_id="vid3", title=TITLE, game_date=date(2026, 10, 5), status="analyzed",
                                              away_team=lg, home_team=kia)
        HighlightScene.objects.create(video=video, start_sec=90, batting_team=kia, batter_name="김도영",
                                      pitcher_name="고우석", event="홈런")
        HighlightScene.objects.create(video=video, start_sec=200, batting_team=lg, batter_name="문보경", event="홈런")
        vs_lg = self.tool.invoke({"player_name": "김도영", "opponent_team_code": "LG", "event": "홈런"})
        self.assertEqual([(i["video_id"], i["matchup"]) for i in vs_lg["items"]], [("vid3", "LG vs KIA")])
        self.assertEqual([i["batter"] for i in self.tool.invoke({"player_name": "고우석"})["items"]], ["김도영"])
        both = self.tool.invoke({"player_name": "김도영", "pitcher_name": "고우석", "event": "홈런"})
        self.assertEqual([i["start_sec"] for i in both["items"]], [90])
        # 투수 칸이 비어 있거나 다른 이름이면 투수 조건을 풀고 relaxed 로 알린다
        loose = self.tool.invoke({"player_name": "김도영", "pitcher_name": "다른투수", "event": "홈런"})
        self.assertEqual((loose["relaxed"], [i["video_id"] for i in loose["items"]]), (["pitcher"], ["vid1"]))  # 최근 1개
        many = self.tool.invoke({"player_name": "김도영", "event": "홈런", "limit": 5})  # 여러 개를 달라고 할 때만
        self.assertEqual([i["video_id"] for i in many["items"]], ["vid1", "vid3"])
        # 사용자가 날짜를 틀리게 기억해도(10/3) 그 경기 장면(10/5)을 찾고 date 를 알린다
        wrong_day = self.tool.invoke({"player_name": "김도영", "opponent_team_code": "LG", "event": "홈런",
                                      "date_from": "2026-10-03", "date_to": "2026-10-03"})
        self.assertEqual((wrong_day["relaxed"], wrong_day["items"][0]["game_date"]), (["date"], "2026-10-05"))
        # 상대 팀을 안 넘겨도 요청 날짜(10/3)에 가까운 10/5 가 최신 10/6 보다 먼저
        nearest = self.tool.invoke({"player_name": "김도영", "event": "홈런", "date_from": "2026-10-03", "date_to": "2026-10-03"})
        self.assertEqual([i["game_date"] for i in nearest["items"]], ["2026-10-05"])  # 바로잡을 때는 하나만
        self.assertEqual(nearest["requested_date"], "2026-10-03")
        exact = self.tool.invoke({"player_name": "김도영", "event": "홈런"})
        self.assertEqual((exact["relaxed"], "note" in exact, "note" in loose), ([], False, True))  # 안내는 조건을 풀 때만
        self.assertEqual(self.tool.invoke({"player_name": "없는선수", "pitcher_name": "고우석"})["relaxed"], [])
        self.assertEqual([i["batter"] for i in self.tool.invoke({"opponent_team_code": "KIA", "event": "홈런"})["items"]], ["문보경"])

    def test_pitcher_only_question_stays_in_pitchers_games(self):
        lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        kt, _ = Team.objects.get_or_create(team_code="KT", defaults={"id": 990003, "team_name_ko": "KT 위즈"})
        kia = Team.objects.get(team_code="KIA")
        game = HighlightVideo.objects.create(video_id="vlg", title=TITLE, game_date=date(2026, 10, 5), status="analyzed", away_team=kia, home_team=lg)
        other = HighlightVideo.objects.create(video_id="vkt", title=TITLE, game_date=date(2026, 10, 5), status="analyzed", away_team=kt, home_team=kia)
        later = HighlightVideo.objects.create(video_id="vlg2", title=TITLE, game_date=date(2026, 10, 6), status="analyzed", away_team=kt, home_team=lg)
        HighlightScene.objects.create(video=later, start_sec=10, batting_team=kt, event="삼진",
                                      description="고우석이 커브로 강백호를 삼진으로 잡는다.")  # 설명만 있어도 고우석 → LG
        HighlightScene.objects.create(video=game, start_sec=800, batting_team=kia, batter_name="김도영", event="홈런")
        HighlightScene.objects.create(video=game, start_sec=100, batting_team=lg, batter_name="문보경", event="홈런")
        HighlightScene.objects.create(video=other, start_sec=50, batting_team=kt, batter_name="데이비슨", event="홈런")
        found = self.tool.invoke({"pitcher_name": "고우석", "event": "홈런", "date_from": "2026-10-05", "date_to": "2026-10-05"})
        self.assertEqual((found["relaxed"], [i["batter"] for i in found["items"]]), (["pitcher"], ["김도영"]))
        unknown = self.tool.invoke({"pitcher_name": "모르는투수", "event": "홈런", "date_from": "2026-10-05", "date_to": "2026-10-05"})
        self.assertEqual(unknown["count"], 0)  # 팀도 모르면 그날 아무 홈런이나 보여 주지 않는다

    def test_unspecified_question_returns_latest_one_and_exact_date_returns_that_day(self):
        latest = self.tool.invoke({"player_name": "김도영"})
        self.assertEqual([(i["video_id"], i["start_sec"]) for i in latest["items"]], [("vid1", 187)])
        that_day = self.tool.invoke({"player_name": "김도영", "date_from": "2026-10-06", "date_to": "2026-10-06"})
        self.assertEqual([i["start_sec"] for i in that_day["items"]], [187, 300])

    def test_unspecified_event_prefers_key_plays_in_game_order(self):
        video = HighlightVideo.objects.get(video_id="vid1")
        HighlightScene.objects.create(video=video, start_sec=30, event="삼진", description="1회 삼진")
        HighlightScene.objects.create(video=video, start_sec=60, event="아웃", description="1회 땅볼 아웃")
        self.assertEqual([i["start_sec"] for i in self.tool.invoke({"team_code": "KIA"})["items"]], [187])
        same_day = self.tool.invoke({"team_code": "KIA", "date_from": "2026-10-06", "date_to": "2026-10-06"})
        self.assertEqual([i["start_sec"] for i in same_day["items"]], [187, 300, 420])  # 1회 삼진·땅볼보다 홈런·안타

    def test_date_only_question_returns_one_key_play_per_game(self):
        other = HighlightVideo.objects.create(video_id="vid9", title=TITLE, game_date=date(2026, 10, 6), status="analyzed")
        HighlightScene.objects.create(video=other, start_sec=40, event="삼진")
        HighlightScene.objects.create(video=other, start_sec=500, event="2루타", rbi=2, description="2타점 적시 2루타")
        day = self.tool.invoke({"date_from": "2026-10-06", "date_to": "2026-10-06"})
        self.assertEqual([(i["video_id"], i["start_sec"]) for i in day["items"]], [("vid1", 187), ("vid9", 500)])

    def test_invalid_event_is_rejected_and_empty_result_keeps_metadata(self):
        self.assertIn("올바르지 않습니다", str(self.tool.invoke({"event": "끝내기"})))
        empty = self.tool.invoke({"player_name": "없는선수"})
        self.assertEqual((empty["count"], empty["analyzed_videos"]), (0, 1))


class DailyCrawlerTest(TestCase):
    def test_off_by_default_and_runs_recent_days_when_enabled(self):
        from crawling import kbo_highlights
        with patch("crawling.kbo_highlights._setup"), patch("llm.highlights.collect") as collect:
            with patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "0"}):
                self.assertIsNone(kbo_highlights.main(today=date(2026, 10, 9)))
            collect.assert_not_called()
            collect.return_value = {"found": 5, "analyzed": 5, "skipped": 0, "failed": 0, "cost": 0.5}
            with patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "1", "HIGHLIGHT_DAILY_BUDGET": "1.5"}):
                kbo_highlights.main(today=date(2026, 10, 9))
            collect.assert_called_once_with(since=date(2026, 10, 6), limit=20, budget=1.5)

    def test_missing_key_does_not_fail_daily_run(self):
        from crawling import kbo_highlights
        with patch("crawling.kbo_highlights._setup"), patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "1"}), \
                patch("llm.highlights.collect", side_effect=highlights.HighlightError("YOUTUBE_API_KEY 가 없습니다.")):
            self.assertIsNone(kbo_highlights.main(today=date(2026, 10, 9)))
