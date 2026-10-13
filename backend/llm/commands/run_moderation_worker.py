import signal
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from services import community_moderation


class Command(BaseCommand):
    help = 'Process persistent community moderation jobs (manual/live).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Drain currently due jobs and exit.')

    def handle(self, *args, **options):
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            while not stopping:
                close_old_connections()
                if not community_moderation.run_one():
                    if options['once']:
                        return
                    time.sleep(2)
        except KeyboardInterrupt:
            return
        finally:
            signal.signal(signal.SIGTERM, previous)
