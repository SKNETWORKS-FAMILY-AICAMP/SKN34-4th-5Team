"""Validated filters for public course lists; legacy stored names remain supported."""
from functools import reduce
from operator import or_

from django.db.models import Q
from rest_framework.exceptions import ValidationError


STADIUM_ALIASES = {
    "JAMSIL": ("잠실",), "GOCHEOK": ("고척",),
    "MUNHAK": ("인천", "문학", "랜더스필드"), "SUWON": ("수원", "위즈파크", "위즈 파크"),
    "DAEJEON": ("대전",), "DAEGU": ("대구",), "GWANGJU": ("광주",),
    "SAJIK": ("사직",), "CHANGWON": ("창원",),
}


def filter_courses(queryset, params):
    for name in ("stadium", "ordering", "limit", "exclude_samples"):
        if len(params.getlist(name)) > 1:
            raise ValidationError({name: "한 개의 값만 입력해 주세요."})
    if "stadium" in params:
        code = params["stadium"].upper()
        if code not in STADIUM_ALIASES:
            raise ValidationError({"stadium": "올바른 구장 코드를 입력해 주세요."})
        aliases = [Q(stadium__icontains=name) for name in STADIUM_ALIASES[code]]
        queryset = queryset.filter(Q(stadium__iexact=code) | reduce(or_, aliases))
    ordering = params.get("ordering", "newest")
    orders = {"newest": ("-created_at", "-id"), "likes": ("-likes", "-created_at", "-id")}
    if ordering not in orders:
        raise ValidationError({"ordering": "newest 또는 likes를 입력해 주세요."})
    exclude = params.get("exclude_samples", "false")
    if exclude not in ("true", "false"):
        raise ValidationError({"exclude_samples": "true 또는 false를 입력해 주세요."})
    if exclude == "true":
        queryset = queryset.filter(is_sample=False)
    queryset = queryset.order_by(*orders[ordering])
    if "limit" in params:
        limit = params["limit"]
        if len(limit) > 3 or not limit.isascii() or not limit.isdigit() or not 1 <= int(limit) <= 100:
            raise ValidationError({"limit": "1부터 100까지의 정수를 입력해 주세요."})
        queryset = queryset[:int(limit)]
    return queryset
