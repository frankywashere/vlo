"""Contracts for isolated long renders; ordinary provider jobs remain 4–15s."""
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .media import Anchor, Target

FPS = 24
MAX_LONG_SECONDS = 60
MIN_CHILD_SECONDS = 4
MAX_CHILD_SECONDS = 15


class LongSetup(BaseModel):
    """Validate the parent independently of the bounded production Setup."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    face_mesh: bool | None = Field(default=None, strict=True)
    enhanced_character_masking: bool = Field(default=True, strict=True)
    easy_mode: bool = Field(default=True, strict=True)
    submit_without_audio: bool = Field(default=False, strict=True)
    add_watermark: bool = Field(default=False, strict=True)
    watermark_bonus_quote: int | None = Field(default=None, ge=0, le=100000, strict=True)
    auto_generate: bool = Field(default=False, strict=True)
    replace_all_people: bool = Field(default=False, strict=True)
    preparation_version: Literal[1, 2] = 2
    trim: tuple[float, float]
    anchor: Anchor | None = None
    targets: list[Target] | None = Field(default=None, min_length=1, max_length=5)
    target_name: str = Field(default="Selected person", min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_range(self):
        start, end = self.trim
        duration = round(end - start, 6)
        if (start < 0 or not MIN_CHILD_SECONDS <= duration <= MAX_LONG_SECONDS
                or not math.isclose(duration, round(duration), abs_tol=0.01)):
            raise ValueError("Select 4–60 seconds, with a whole-second length.")
        if self.targets and self.replace_all_people:
            raise ValueError("Choose individual people or replace everyone, not both.")
        if self.targets and (len({target.id for target in self.targets}) != len(self.targets)
                            or any(not start <= target.anchor.time < end for target in self.targets)):
            raise ValueError("Give each person a unique ID and select them inside the trimmed section.")
        selection = self.anchor or (self.targets[0].anchor if self.targets else None)
        if not self.replace_all_people and (selection is None or not start <= selection.time < end):
            raise ValueError("Select the person inside the trimmed section.")
        return self


def plan_children(duration: int) -> list[dict]:
    """Use balanced whole-second windows; never create a 1–3 second tail."""
    if type(duration) is not int or not MIN_CHILD_SECONDS <= duration <= MAX_LONG_SECONDS:
        raise ValueError("Long-render duration must be an integer between 4 and 60 seconds.")
    count = math.ceil(duration / MAX_CHILD_SECONDS)
    base, extra = divmod(duration, count)
    result, start_frame = [], 0
    for index in range(count):
        seconds = base + (1 if index < extra else 0)
        frames = seconds * FPS
        result.append({"index": index, "start_frame": start_frame,
                       "end_frame": start_frame + frames,
                       "start_seconds": start_frame // FPS, "duration": seconds,
                       "frames": frames})
        start_frame += frames
    return result
