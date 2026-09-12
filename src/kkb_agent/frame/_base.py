"""Shared immutable primitives for the public frame contracts."""

from datetime import UTC
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

Identifier = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
Version = Annotated[int, Field(strict=True, ge=0)]
Timestamp = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
Scalar = StrictInt | StrictFloat | StrictStr | StrictBool | None


class Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, revalidate_instances="always"
    )


class MetadataEntry(Contract):
    """An immutable extension entry; namespaced keys can describe source-specific details."""

    key: Identifier
    value: Scalar
