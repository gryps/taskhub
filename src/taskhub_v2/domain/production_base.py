from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProductionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=80)
    created_by: str = Field(default="system", min_length=1, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    source_ids: list[str] = Field(default_factory=list)
    content_digest: str = Field(default="", max_length=128)
