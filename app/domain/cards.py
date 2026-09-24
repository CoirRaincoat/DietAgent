"""Independent API card contracts; no dependency on domain recipe models."""

from pydantic import BaseModel, ConfigDict, Field


class DishCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    subtitle: str
    badges: list[str] = Field(default_factory=list)
    image_url: None = None
    cooking_minutes: None = None
    servings: None = None


class CookingStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1)
    description: str = Field(min_length=1)


class RecipeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    source_row: int = Field(ge=1)
    fingerprint: str