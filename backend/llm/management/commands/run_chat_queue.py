"""Single-host supervisor. Inherited flock proves child death across container restarts."""
import fcntl
from datetime import timedelta
import multiprocessing
import os
from pathlib import Path
import signal
import time
import uuid

from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone

from llm.models import ChatRequest
from llm.service import chat_queue


def child(request_id, attempt, lock_fd, deadline, owner_fd, sibling_fds):
    os.close(owner_fd)
    for fd in sibling_fds:
        os.close(fd)
    result = 1
    # Keep inherited lock open for the full provider lifetime (including its threads).
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(deadline)
    try:
        if os.name == "posix" and Path("/proc/self").exists():
            import ctypes
            parent = os.getppid()
            ctypes.CDLL(None).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG; no elevated capability
            if os.getppid() != parent:
                os._exit(125)
        connections.close_all()
        chat_queue.execute(request_id, attempt)
        result = 0
    finally:
        connections.close_all()
        # subprocess exits even if a provider helper thread ignores cooperative cancellation
        os._exit(result)


class Command(BaseCommand):
    help = "Run durable chat requests; one provider subprocess per wallet, no browser required"

    def add_arguments(self, parser):
        parser.add_argument("--state-dir", default=os.getenv("CHAT_QUEUE_STATE_DIR", "/var/lib/chat-queue"))
        parser.add_argument("--deadline", type=int, default=600)
        parser.add_argument("--concurrency", type=int, default=4)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if options["deadline"] < 1 or options["concurrency"] < 1:
            raise ValueError("positive deadline/concurrency required")
        directory = Path(options["state_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        scope_file = directory / "scope"
        # Exclusive directory-owner lock avoids competing startup scope initialization.
        owner = open(directory / "supervisor.lock", "a+")
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if scope_file.exists():
            scope = scope_file.read_text().strip()
        else:
            scope = str(uuid.uuid4())
            scope_file.write_text(scope)
        uuid.UUID(scope)
        context = multiprocessing.get_context("fork")
        active = {}
        stopping = [False]
        def stop(*_):
            stopping[0] = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def lock_for(row):
            handle = open(directory / f"{row.id}.lock", "a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                handle.close()
                return None

        try:
            while not stopping[0]:
                connections.close_all()
                chat_queue.cleanup()
                # Only same persistent scope + acquired inherited flock proves prior child died.
                for row in ChatRequest.objects.filter(status="running", worker__scope=scope):
                    if str(row.id) in active:
                        continue
                    proof = lock_for(row)
                    if proof:
                        try:
                            chat_queue.reconcile(row.id, row.attempt, interrupted=True)
                        finally:
                            proof.close()
                for request_id, (process, proof, started, attempt) in list(active.items()):
                    row = ChatRequest.objects.filter(id=request_id, attempt=attempt).first()
                    expired = time.monotonic() - started > options["deadline"]
                    cancelled = row is None or row.session_id is None or row.cancel_requested
                    if (expired or cancelled) and process.is_alive():
                        process.kill()
                    if not process.is_alive():
                        process.join()
                        try:
                            chat_queue.reconcile(request_id, attempt, interrupted=expired or cancelled or process.exitcode != 0)
                        finally:
                            proof.close()
                            del active[request_id]
                    else:
                        ChatRequest.objects.filter(id=request_id, attempt=attempt, status="running").update(heartbeat_at=timezone.now(), lease_expires_at=timezone.now() + timedelta(seconds=30))
                while len(active) < options["concurrency"]:
                    row = chat_queue.claim(scope)
                    if row is None:
                        break
                    proof = lock_for(row)
                    if proof is None:
                        raise RuntimeError("new request lock unexpectedly held")
                    connections.close_all()
                    process = context.Process(target=child, args=(str(row.id), row.attempt, proof.fileno(), options["deadline"], owner.fileno(), [entry[1].fileno() for entry in active.values()]))
                    process.start()
                    active[str(row.id)] = process, proof, time.monotonic(), row.attempt
                (directory / "heartbeat").write_text(str(time.time()))
                if options["once"] and not active:
                    return
                time.sleep(0.5)
        finally:
            for request_id, (process, proof, _, attempt) in active.items():
                if process.is_alive():
                    process.kill()
                process.join()
                try:
                    chat_queue.reconcile(request_id, attempt, interrupted=True)
                finally:
                    proof.close()
            owner.close()
