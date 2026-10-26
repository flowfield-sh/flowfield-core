"""Document selection and immutable provenance shared by planning and execution."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from flowfield.project_config import Identifier, Title


class DocumentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)
    id: Identifier
    revision: int | None = Field(
        default=None,
        ge=1,
        description="Legacy selection revision. Preparation and reconciliation use the latest "
        "revision; saved execution and review evidence retain their exact revisions.",
    )
    purpose: Literal["required", "reference"] = "required"

    @model_validator(mode="after")
    def required_guidance(self) -> Self:
        if self.id == "guidance" and self.purpose != "required":
            raise ValueError("Workspace guidance is always required.")
        return self


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
    purpose: Literal["required", "reference"] = "required"


class DocumentUpdate(BaseModel):
    workspace_id: str
    id: str
    title: str
    prepared_revision: int
    latest_revision: int
    purpose: Literal["required", "reference"]
