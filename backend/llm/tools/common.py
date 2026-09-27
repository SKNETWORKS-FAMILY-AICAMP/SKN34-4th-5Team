"""domain tools(baseball/stadium/community/travel/weather)가 공유하는 모델 독립적 헬퍼.

각 도메인 모듈이 개별로 들고 있던 동일한 _json/_rows/_result/_safe/_tool/ToolInput/
LimitInput 정의를 한 곳으로 모은다. 스키마 이름, 도구 이름, 예외 동작은 그대로 유지한다
(이 파일은 헬퍼만 옮긴 것이고 동작을 바꾸지 않는다).
"""
from decimal import Decimal
from functools import wraps
from uuid import UUID

from django.db import DatabaseError
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

INVALID_INPUT = "도구 입력 형식이 올바르지 않습니다. 인자 설명을 확인하세요."
DB_ERROR = "저장된 정보를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _json(value):
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _rows(queryset, fields, limit):
    return [{key: _json(value) for key, value in row.items()} for row in queryset.values(*fields)[:limit]]


def _result(items, **metadata):
    return {**metadata, "count": len(items), "items": items}


def _safe(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except DatabaseError:
            raise ToolException(DB_ERROR) from None

    return wrapped


def _tool(function, name, description, schema):
    return StructuredTool.from_function(
        _safe(function), name=name, description=description, args_schema=schema,
        handle_tool_error=True, handle_validation_error=INVALID_INPUT,
    )


class ToolInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class LimitInput(ToolInput):
    limit: StrictInt = Field(default=20, ge=1, le=100)
