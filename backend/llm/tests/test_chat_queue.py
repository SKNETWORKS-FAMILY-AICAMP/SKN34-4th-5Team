"""Real PostgreSQL queue checks; provider frames below are explicit deterministic QA fixtures."""
import uuid
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from django.db import connections
from django.test import TransactionTestCase
from langchain_core.messages import AIMessage

from llm.models import ChatSession, ChatRequest, UsageCharge
from llm.service import chat_queue, usage
from llm.service.chat_thread import ChatThread


class QueueTests(TransactionTestCase):
    def setUp(self):
        ChatThread.setup()
        self.session = ChatSession.objects.create(guest=uuid.uuid4())
        self.payload = {"version": "v2", "content": "QA first", "context": {"stadium": "잠실"}, "tool_group_ids": ["weather"], "attachment_ids": []}

    def admit(self, content=None, session=None):
        return chat_queue.enqueue(session or self.session, uuid.uuid4(), {**self.payload, "content": content or self.payload["content"]})

    def test_idempotency_cap_and_tombstone(self):
        key = uuid.uuid4()
        first = chat_queue.enqueue(self.session, key, self.payload)
        self.assertEqual(chat_queue.enqueue(self.session, key, self.payload).pk, first.pk)
        with self.assertRaises(chat_queue.Conflict):
            chat_queue.enqueue(self.session, key, {**self.payload, "content": "changed"})
        self.admit("second")
        with self.assertRaises(chat_queue.Conflict):
            self.admit("third")
        chat_queue.change(self.session, key)
        self.assertEqual(chat_queue.enqueue(self.session, key, self.payload).status, "cancelled")

    def test_fifo_claims_wallet_and_independent_owner(self):
        first = self.admit()
        sibling = ChatSession.objects.create(guest=self.session.guest)
        second = self.admit("second", sibling)
        third = self.admit("independent", ChatSession.objects.create(guest=uuid.uuid4()))
        def claim():
            try:
                row = chat_queue.claim("qa")
                return row.id if row else None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(set(claimed), {first.id, third.id})
        self.assertEqual(ChatRequest.objects.get(pk=second.pk).status, "queued")
        self.assertEqual(UsageCharge.objects.filter(status="reserved").count(), 2)

    def test_fresh_history_stable_identity_and_events_redacted(self):
        previous = ChatThread(self.session.id)
        prefix, turns, human = previous.ask("previous")
        previous.update([AIMessage("QA completed predecessor", id="previous-answer")], {human.id: {"status": "completed", "answer_id": "previous-answer"}})
        queued = self.admit()
        row = chat_queue.claim("qa")
        seen = []
        def frames(graph_input, run):
            seen.extend(graph_input["messages"])
            run["answer"] = "QA deterministic answer"
            yield "tool", {"id": "qa-tool", "tool_name": "weather", "status": "completed", "detail": {"args": {"secret": "private"}, "result": "private"}, "title": "private"}
            yield "delta", {"text": "QA deterministic answer"}
        with patch("llm.service.chat_v2._frames", frames):
            chat_queue.execute(row.id, row.attempt)
        chat_queue.reconcile(row.id, row.attempt)
        queued.refresh_from_db()
        self.assertEqual(queued.status, "completed")
        self.assertTrue(any(m.content == "QA completed predecessor" for m in seen))
        messages, turns = ChatThread(self.session.id).state()
        self.assertEqual(sum(m.id == str(row.human_id) for m in messages), 1)
        self.assertEqual(turns[str(row.human_id)]["answer_id"], str(row.answer_id))
        self.assertNotIn("private", str(list(row.events.values_list("data", flat=True))))

    def test_stale_checkpoint_cancel_and_unknown_recovery(self):
        row = self.admit()
        row = chat_queue.claim("qa")
        thread = ChatThread(self.session.id)
        thread.queue_request, thread.queue_attempt = str(row.id), row.attempt
        _, _, human = thread.ask("QA pending", human_id=str(row.human_id))
        ChatRequest.objects.filter(pk=row.pk).update(cancel_requested=True)
        from llm.service.chat_runs import Stopped
        with self.assertRaises(Stopped):
            thread.update([AIMessage("late", id=str(row.answer_id))], {human.id: {"status": "completed"}})
        UsageCharge.objects.filter(pk=row.charge_id).update(input_tokens=9, output_tokens=3, calls=1)
        chat_queue.reconcile(row.id, row.attempt, interrupted=True)
        row.refresh_from_db()
        row.charge.refresh_from_db()
        self.assertEqual(row.status, "cancelled")
        self.assertEqual(row.charge.status, "settled_unknown")
        self.assertEqual(row.charge.charged_tokens, 12)
        self.assertFalse(usage.balance(guest=self.session.guest)["active_turn"])

    def test_authenticated_queue_api_and_event_accept(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.cookies["guest_id"] = str(self.session.guest)
        path = f"/api/v2/chat/sessions/{self.session.id}/requests/"
        key = str(uuid.uuid4())
        body = {"request_id": key, "content": "QA input", "tool_group_ids": ["weather"]}
        self.assertEqual(client.post(path, body, format="json").status_code, 202)
        self.assertEqual(client.post(path, body, format="json").status_code, 202)
        self.assertEqual(client.post(path, {**body, "content": "different"}, format="json").status_code, 409)
        stranger = APIClient()
        self.assertEqual(stranger.get(path).status_code, 404)
        self.assertEqual(stranger.get(path + key + "/events/").status_code, 404)
        response = client.get(path + key + "/events/", HTTP_ACCEPT="text/event-stream")
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(ChatRequest.objects.get(id=key).status, "queued")
        self.assertEqual(client.patch(path + key + "/", {"revision": 0, "content": "QA edited"}, format="json").status_code, 200)
        self.assertEqual(client.patch(path + key + "/", {"revision": 0, "content": "QA stale"}, format="json").status_code, 409)
        self.assertEqual(client.delete(path + key + "/").status_code, 200)
        self.assertEqual(client.post(path, body, format="json").data["status"], "cancelled")

    def test_exhaustion_and_deleted_running_session_release_known_usage(self):
        row = self.admit()
        wallet = usage._owner(self.session)
        from llm.models import UsageWallet
        UsageWallet.objects.filter(**wallet).update(used_tokens=500000)
        self.assertIsNone(chat_queue.claim("qa"))
        row.refresh_from_db()
        self.assertEqual(row.status, "failed")
        UsageWallet.objects.filter(**wallet).update(used_tokens=0)
        live = self.admit("deleted session")
        live = chat_queue.claim("qa")
        self.session.delete()
        live.refresh_from_db()
        self.assertIsNone(live.session_id)
        chat_queue.reconcile(live.id, live.attempt, interrupted=True)
        self.assertFalse(ChatRequest.objects.filter(pk=live.pk).exists())
        self.assertFalse(UsageCharge.objects.filter(pk=live.charge_id, status="reserved").exists())

    def test_blocked_prefix_does_not_starve_independent_wallet(self):
        for _ in range(33):
            blocked = ChatSession.objects.create(guest=uuid.uuid4())
            usage.reserve(blocked)
            self.admit("blocked by legacy reservation", blocked)
        available = self.admit("independent wallet")
        self.assertEqual(chat_queue.claim("qa").id, available.id)

    def test_meter_persists_known_and_inflight_usage_and_fences_old_attempt(self):
        row = self.admit()
        row = chat_queue.claim("qa")
        meter = usage.Meter()
        meter.charge = row.charge
        meter.charge.queue_request, meter.charge.queue_attempt = row.id, row.attempt
        meter.check("first")
        meter.record((9, 3), "first")
        meter.check("interrupted")
        row.charge.refresh_from_db()
        self.assertEqual((row.charge.input_tokens, row.charge.output_tokens, row.charge.unknown_calls), (9, 3, 1))
        chat_queue.reconcile(row.id, row.attempt, interrupted=True)
        from llm.service.chat_runs import Stopped
        with self.assertRaises(Stopped):
            meter.record((50, 50), "interrupted")
        row.charge.refresh_from_db()
        self.assertEqual(row.charge.charged_tokens, 12)

    def test_completion_commit_wins_cancel_and_crash_known_accounting(self):
        row = self.admit()
        row = chat_queue.claim("qa")
        def frames(graph_input, run):
            usage.record_external(9, 3)
            run["answer"] = "QA completed"
            yield "delta", {"text": "QA completed"}
        with patch("llm.service.chat_v2._frames", frames), patch("llm.service.chat_queue.append"):
            chat_queue.execute(row.id, row.attempt)
        row.refresh_from_db()
        row.worker = {"scope": "qa"}  # crash after checkpoint commit before finished marker
        row.save(update_fields=["worker"])
        chat_queue.change(self.session, row.id)
        row.refresh_from_db()
        self.assertFalse(row.cancel_requested)
        chat_queue.reconcile(row.id, row.attempt, interrupted=True)
        row.refresh_from_db()
        row.charge.refresh_from_db()
        self.assertEqual(row.status, "completed")
        self.assertTrue(row.events.filter(event="done").exists())
        self.assertEqual((row.charge.charged_tokens, row.charge.unknown_calls), (12, 0))

    def test_deletion_scrubs_running_and_purges_terminal_requests(self):
        row = self.admit()
        row = chat_queue.claim("qa")
        chat_queue.append(row.id, row.attempt, "delta", {"text": "private QA"})
        terminal = self.admit("private cancelled")
        chat_queue.change(self.session, terminal.id)
        self.session.delete()
        row.refresh_from_db()
        self.assertEqual((row.payload, row.accepted_payload), ({}, {}))
        self.assertFalse(row.events.exists())
        self.assertFalse(ChatRequest.objects.filter(pk=terminal.pk).exists())
        chat_queue.reconcile(row.id, row.attempt, interrupted=True)
        self.assertFalse(ChatRequest.objects.filter(pk=row.pk).exists())

    def test_account_deletion_does_not_recreate_accounting_or_block_recovery(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create(username="queue-account-delete", nickname="QA")
        session = ChatSession.objects.create(user=user)
        row = self.admit("account private", session)
        row = chat_queue.claim("qa")
        user.delete()
        row.refresh_from_db()
        self.assertIsNone(row.charge_id)
        self.assertEqual(row.payload, {})
        chat_queue.reconcile(row.id, row.attempt, interrupted=True)
        self.assertFalse(ChatRequest.objects.filter(pk=row.pk).exists())
        self.assertIsNotNone(chat_queue.claim("qa") if self.admit("next owner") else None)

    def test_public_edit_arbitration_allows_same_session_waiting(self):
        from llm.service import chat_v2
        thread = ChatThread(self.session.id)
        _, _, human = thread.ask("original")
        row = self.admit("waiting")
        def frames(graph_input, run):
            run["answer"] = "QA edited"
            yield "delta", {"text": "QA edited"}
        with patch("llm.service.chat_v2._frames", frames):
            list(chat_v2.message_update(self.session, human.id, "edited"))
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")

    def test_concurrent_collector_delete_waits_for_checkpoint_session_fence(self):
        import threading
        from django.db import transaction, connection
        locked, deleting = threading.Event(), threading.Event()
        session_id = self.session.id
        row = self.admit()
        row = chat_queue.claim("qa")
        def writer():
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    ChatSession.objects.select_for_update().get(pk=session_id)
                    locked.set()
                    self.assertTrue(deleting.wait(2))
                    thread = ChatThread(session_id)
                    thread.queue_request, thread.queue_attempt = row.id, row.attempt
                    thread.ask("QA writer", human_id=str(row.human_id))
            finally:
                connections.close_all()
        def delete():
            try:
                self.assertTrue(locked.wait(2))
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    deleting.set()
                    ChatSession.objects.get(pk=session_id).delete()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(writer), pool.submit(delete)]
            for future in futures:
                future.result(timeout=8)
        row.refresh_from_db()
        self.assertIsNone(row.session_id)
        self.assertEqual(row.payload, {})
        self.assertFalse(ChatThread(session_id).state()[0])
        chat_queue.reconcile(row.id, row.attempt, interrupted=True)

    def test_continuation_rebases_latest_course_but_explicit_context_kept(self):
        for continuation in (True, False):
            session = ChatSession.objects.create(guest=uuid.uuid4())
            row = self.admit("QA route", session)
            row.payload = {**row.payload, "context": {"currentCourse": {"places": []}}, "continuation": continuation}
            row.accepted_payload = row.payload
            row.save()
            row = chat_queue.claim("qa")
            seen = []
            def frames(graph_input, run):
                seen.append(graph_input.get("context"))
                run["answer"] = "QA route"
                yield "delta", {"text": "QA route"}
            latest = {"places": [{"name": "QA latest"}]}
            with patch("llm.v1.rag.course.memory.restore", return_value={"current": latest}), patch("llm.service.chat_v2._frames", frames):
                chat_queue.execute(row.id, row.attempt)
            chat_queue.reconcile(row.id, row.attempt)
            self.assertEqual(seen[0]["currentCourse"], latest if continuation else {"places": []})

    def test_history_edit_cancels_waiting(self):
        thread = ChatThread(self.session.id)
        _, _, human = thread.ask("history")
        row = self.admit()
        thread.edit(human.id, "edited history")
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")
