import math

from rest_framework import serializers

from llm.models import ChatMessage, ChatToolCall

CONTEXT_INTENT_CHOICES = ("route", "baseball", "stadium")


def _validate_context(value):
    """POST/PUT content 와 함께 오는 선택 사항 context 검증. 없으면 그대로 통과 (하위 호환).

    형식: {"stadium"?: str(<=100), "intent"?: "route"|"baseball"|"stadium",
           "origin"?: {"lat": float(-90~90), "lng": float(-180~180)}}
    origin 이 있으면 lat/lng 둘 다 있어야 하고, 둘 다 유한한 실수(math.isfinite)여야 한다.
    DRF FloatField 의 min_value/max_value 비교는 NaN 과 항상 False 라 안 걸리고
    "NaN"/"Infinity" 문자열도 float() 변환만으로 통과하므로, 범위 검사 전에 isfinite 로 막는다.
    JSON 정수 리터럴이 float 로 표현 불가능할 만큼 크면 float() 가 OverflowError 를 던진다
    (TypeError/ValueError 와 별도 예외라 같이 잡아야 한다).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise serializers.ValidationError("context 는 객체여야 합니다.")

    errors = {}
    cleaned = {}

    if "stadium" in value:
        stadium = value["stadium"]
        if not isinstance(stadium, str) or not stadium or len(stadium) > 100:
            errors["stadium"] = "stadium 은 1~100자 문자열이어야 합니다."
        else:
            cleaned["stadium"] = stadium

    if "intent" in value:
        intent = value["intent"]
        if intent not in CONTEXT_INTENT_CHOICES:
            errors["intent"] = f"intent 는 {CONTEXT_INTENT_CHOICES} 중 하나여야 합니다."
        else:
            cleaned["intent"] = intent

    if "origin" in value:
        origin = value["origin"]
        if not isinstance(origin, dict) or "lat" not in origin or "lng" not in origin:
            errors["origin"] = "origin 은 lat/lng 이 모두 있는 객체여야 합니다."
        else:
            origin_errors = {}
            origin_cleaned = {}
            for key, lo, hi in (("lat", -90, 90), ("lng", -180, 180)):
                raw = origin[key]
                try:
                    num = float(raw)
                except (TypeError, ValueError, OverflowError):
                    origin_errors[key] = "유한한 실수여야 합니다."
                    continue
                if not math.isfinite(num):
                    origin_errors[key] = "좌표는 유한한 실수여야 합니다."
                elif not (lo <= num <= hi):
                    origin_errors[key] = f"{lo}~{hi} 범위여야 합니다."
                else:
                    origin_cleaned[key] = num
            if origin_errors:
                errors["origin"] = origin_errors
            else:
                cleaned["origin"] = origin_cleaned

    if errors:
        raise serializers.ValidationError(errors)
    return cleaned


class ChatMessageInputSerializer(serializers.Serializer):
    """POST /messages/ 요청 바디 검증 (content + 선택 사항 context)."""
    content = serializers.CharField(max_length=2200, allow_blank=False)
    context = serializers.DictField(required=False, allow_null=True)

    def validate_content(self, value):
        # CharField 는 기본적으로 int/float 등도 str() 로 강제 변환해 통과시킨다.
        # content 는 실제 문자열 입력만 허용한다.
        if not isinstance(self.initial_data.get("content"), str):
            raise serializers.ValidationError("content 는 문자열이어야 합니다.")
        return value

    def validate_context(self, value):
        return _validate_context(value)


class ChatMessageUpdateSerializer(ChatMessageInputSerializer):
    """PUT /messages/ 요청 바디 검증 (content + message_id + 선택 사항 context)."""
    # ChatMessage.id 는 정수 PK (ChatSession.id 만 UUID). 존재하지 않는 id는 여기서 안 걸리고
    # 서비스 계층의 ChatMessage.DoesNotExist 로 처리된다 (기존 동작 유지, 이 파일 책임 밖).
    message_id = serializers.IntegerField()


class ChatMessageDeleteSerializer(serializers.Serializer):
    """DELETE /messages/ 요청 바디 검증 (message_id만 필요)."""
    message_id = serializers.IntegerField()


class ChatToolCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatToolCall
        fields = (
            "id",
            "tool_name",
            "status",
            "created_at",
        )
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    content = serializers.CharField(
        source="message",
        max_length=2200,
    )

    tools = ChatToolCallSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "sequence_no",
            "role",
            "content",
            "status",
            "tools",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "sequence_no",
            "role",
            "status",
            "tools",
            "created_at",
            "updated_at",
        )
