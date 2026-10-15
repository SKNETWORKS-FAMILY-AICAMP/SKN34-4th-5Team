from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .models import FREE_CATEGORIES, TEAM_CATEGORIES, TEAM_CODES


class DraftInput(BaseModel):
    is_trade: bool = Field(default=False, alias='isTrade')
    is_advertisement: bool = Field(default=False, alias='isAdvertisement')
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    board: Literal["free", "teams"]
    team_code: str = Field(default="", alias="teamCode", max_length=2)
    category: str = Field(default="", max_length=20)
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=20000)

    @field_validator("team_code")
    @classmethod
    def normalize_team_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_board_fields(self):
        if self.board == "free" and self.team_code:
            raise ValueError("자유게시판은 팀 코드를 사용할 수 없습니다.")
        if self.board == "teams" and self.team_code not in TEAM_CODES:
            raise ValueError("올바른 팀 코드를 입력해 주세요.")
        allowed_categories = FREE_CATEGORIES if self.board == "free" else TEAM_CATEGORIES
        if self.category and self.category not in allowed_categories:
            raise ValueError("올바른 카테고리를 입력해 주세요.")
        return self


class DraftPatch(BaseModel):
    is_trade: bool | None = Field(default=None, alias='isTrade')
    is_advertisement: bool | None = Field(default=None, alias='isAdvertisement')
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revision: StrictInt = Field(gt=0)
    board: Literal["free", "teams"] | None = None
    team_code: str | None = Field(default=None, alias="teamCode", max_length=2)
    category: str | None = Field(default=None, max_length=20)
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=20000)
    image_ids: list[UUID] | None = Field(default=None, alias="imageIds", max_length=10)

    @field_validator("team_code")
    @classmethod
    def normalize_team_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("image_ids")
    @classmethod
    def unique_image_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("이미지 ID를 중복해서 입력할 수 없습니다.")
        return value


class DraftOutput(BaseModel):
    is_trade: bool = Field(alias='isTrade')
    is_advertisement: bool = Field(alias='isAdvertisement')
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    board: Literal["free", "teams"]
    team_code: str = Field(alias="teamCode")
    category: str
    title: str
    content: str
    revision: int
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    image_ids: list[UUID] = Field(alias="imageIds")


class DraftPublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: StrictInt = Field(gt=0)
