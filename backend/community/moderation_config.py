import os
from .models import ModerationSettings


def current_mode():
    stored = ModerationSettings.objects.filter(pk=1).values_list('mode', flat=True).first()
    if stored:
        return stored
    return 'openai'


def mode_info():
    return {'mode': current_mode(), 'automaticAvailable': bool(os.getenv('OPENAI_API_KEY', '').strip()),
            'model': 'omni-moderation-latest'}
