from django.core.management.base import BaseCommand

from accounts.models import CustomUser
from accounts.point_policy import SIGNUP, grant_definition
from accounts.point_service import grant_points


class Command(BaseCommand):
    help = "기존 활성 회원에게 최초 가입 포인트를 멱등적으로 지급합니다."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="지급 대상만 출력합니다.")

    def handle(self, *args, **options):
        users = CustomUser.objects.filter(is_active=True).order_by("pk")
        if options["dry_run"]:
            self.stdout.write(f"대상 회원: {users.count()}명")
            for user in users.iterator():
                self.stdout.write(f"{user.pk}: {user.username}")
            return
        created = 0
        for user in users.iterator():
            _, _, was_created = grant_points(user=user, **grant_definition(SIGNUP, "initial"))
            created += was_created
        self.stdout.write(self.style.SUCCESS(f"가입 보상 지급 완료: {created}명"))
