from django.db.models import F
from django.utils.crypto import salted_hmac

from .models import GuestChatUsage

GUEST_QUESTION_LIMIT = 2


def guest_identity(ip):
    # Never persist a raw IP; stable SECRET_KEY preserves the identity across restarts.
    return salted_hmac("llm.guest-quota.v1", ip, algorithm="sha256").hexdigest()


def guest_remaining(ip):
    used = GuestChatUsage.objects.filter(identity=guest_identity(ip)).values_list("used", flat=True).first() or 0
    return max(0, GUEST_QUESTION_LIMIT - used)


def reserve_guest_question(ip):
    identity = guest_identity(ip)
    GuestChatUsage.objects.get_or_create(identity=identity)
    # Conditional UPDATE is atomic: concurrent requests cannot exceed the quota.
    return bool(GuestChatUsage.objects.filter(
        identity=identity, used__lt=GUEST_QUESTION_LIMIT,
    ).update(used=F("used") + 1))
