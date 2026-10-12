"""Document selection and immutable provenance shared by planning and execution."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from flowfield.project_config import Identifier, Title


class DocumentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)
    id: Identifier
    revision: int | None = Field(default=None, ge=1)


def unique_references(value: list[DocumentReference]) -> list[DocumentReference]:
    if len({item.id for item in value}) != len(value):
        raise ValueError("Choose each document once.")
    return sorted(value, key=lambda item: item.id)


DocumentReferences = Annotated[
    list[DocumentReference], Field(max_length=21), AfterValidator(unique_references)
]


class DocumentSnapshot(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    workspace_id: Identifier
    id: Identifier
    revision: int = Field(ge=1)
    title: Title
    characters: int
    bytes: int
    sha256: str
