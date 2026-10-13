import os
from django.conf import settings
from .models import ModerationSettings


def current_mode():
    stored = ModerationSettings.objects.filter(pk=1).values_list('mode', flat=True).first()
    if stored:
        return stored
    return 'openai' if settings.COMMUNITY_MODERATION_MODE in ('live', 'openai') else 'manual'


def mode_info():
    return {'mode': current_mode(), 'automaticAvailable': bool(os.getenv('OPENAI_API_KEY', '').strip()),
            'model': 'omni-moderation-latest'}
