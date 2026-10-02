from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APITransactionTestCase

from .chat_service import ChatService
from .guest_quota import guest_remaining, reserve_guest_question
from .models import GuestChatUsage


@override_settings(CHAT_GUEST_RATE_LIMIT=10, CHAT_GUEST_RATE_WINDOW=60, CHAT_TRUST_PROXY_HEADERS=False)
class GuestQuotaTests(APITransactionTestCase):
    url = "/api/v1/chat/guest/"
    ip = "203.0.113.41"

    def setUp(self):
        cache.clear()

    def send(self, ip=None, payload=None):
        return self.client.post(self.url, payload or {"messages": [{"role": "user", "content": "구장 알려줘"}]},
                                format="json", REMOTE_ADDR=ip or self.ip)

    def test_two_questions_then_block_even_after_cache_clear(self):
        with patch.object(ChatService, "stream_with_history", return_value=iter(["답"])):
            response = self.send()
            self.assertEqual(response.status_code, 200)
            self.assertIn("답", b"".join(response.streaming_content).decode())
        with patch.object(ChatService, "stream_with_history", return_value=iter(["두 번째"])):
            response = self.send()
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        cache.clear()
        with patch.object(ChatService, "stream_with_history") as model:
            response = self.send()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data["code"], "guest_quota_exhausted")
            model.assert_not_called()
        self.assertEqual(self.client.get(self.url, REMOTE_ADDR=self.ip).data["remaining"], 0)
        self.assertEqual(guest_remaining("203.0.113.42"), 2)
        self.assertNotIn(self.ip, GuestChatUsage.objects.get().identity)

    def test_validation_and_existing_rate_limit_do_not_consume_quota(self):
        self.assertEqual(self.send(payload={"messages": []}).status_code, 400)
        self.assertEqual(guest_remaining(self.ip), 2)
        with patch("llm.views.guest_rate_limited", return_value=True):
            self.assertEqual(self.send().status_code, 429)
        self.assertEqual(guest_remaining(self.ip), 2)

    @override_settings(CHAT_GUEST_RATE_LIMIT=1)
    def test_existing_rate_limit_still_precedes_lifetime_limit(self):
        response = self.send()
        response.close()
        self.assertEqual(self.send().status_code, 429)
        self.assertEqual(guest_remaining(self.ip), 1)

    def test_member_does_not_consume_guest_allowance(self):
        user = get_user_model().objects.create_user(username="quota-member", password="test-only")
        self.client.force_authenticate(user)
        for _ in range(3):
            response = self.send()
            self.assertEqual(response.status_code, 200)
            response.close()
        self.assertFalse(GuestChatUsage.objects.exists())

    @override_settings(CHAT_TRUST_PROXY_HEADERS=False)
    def test_untrusted_ip_header_cannot_reset_allowance(self):
        reserve_guest_question(self.ip)
        reserve_guest_question(self.ip)
        response = self.client.post(self.url, {"messages": [{"role": "user", "content": "질문"}]},
                                    format="json", REMOTE_ADDR=self.ip, HTTP_X_REAL_IP="203.0.113.99")
        self.assertEqual(response.status_code, 403)

    def test_cancelled_request_counts_and_status_lookup_does_not(self):
        for _ in range(3):
            self.assertEqual(self.client.get(self.url, REMOTE_ADDR=self.ip).data["remaining"], 2)
        response = self.send()
        response.close()
        self.assertEqual(guest_remaining(self.ip), 1)


class GuestQuotaConcurrencyTests(TransactionTestCase):
    def test_concurrent_reservations_cannot_exceed_two(self):
        def reserve(_):
            close_old_connections()
            try:
                return reserve_guest_question("203.0.113.77")
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(results), 2)
        self.assertEqual(guest_remaining("203.0.113.77"), 0)
