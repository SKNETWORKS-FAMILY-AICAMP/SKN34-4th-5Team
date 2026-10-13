"""One-shot entry point for the server's existing scheduler."""
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from community.models import ModerationJob
from services.community_moderation import enqueue_unchecked


class Command(BaseCommand):
    help = 'Queue unchecked posts/comments, run existing moderation once, then exit.'

    def handle(self, *args, **options):
        counts = enqueue_unchecked()
        self.stdout.write(f"Queued posts={counts['posts']} comments={counts['comments']}")
        call_command('run_moderation_worker', once=True, stdout=self.stdout, stderr=self.stderr)
        current = ModerationJob.objects.filter(
            Q(post__content_revision=F('revision')) | Q(comment__content_revision=F('revision')))
        remaining = {state: current.filter(state=state).count()
                     for state in ('queued', 'running', 'failed')}
        self.stdout.write(f'Remaining jobs: {remaining}; failed jobs need explicit retry.')
