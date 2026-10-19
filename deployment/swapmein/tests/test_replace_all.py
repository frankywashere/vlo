from io import BytesIO
import zipfile

import numpy as np
from PIL import Image
from pydantic import ValidationError
import pytest

from swapmein.errors import PublicError
from swapmein.media import Setup
from swapmein.provider import generation_payload
from swapmein.tracking import merge_all_people_masks


def write_masks(path, arrays):
    with zipfile.ZipFile(path, "w") as archive:
        for index, labels in enumerate(arrays):
            buffer = BytesIO()
            Image.fromarray(labels.astype(np.uint16)).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())


def test_all_people_needs_no_tap_but_individual_selection_still_does():
    assert Setup(trim=[1.3, 6.3], replace_all_people=True).anchor is None
    with pytest.raises(ValidationError):
        Setup(trim=[1.3, 6.3])
    with pytest.raises(ValidationError):
        Setup(trim=[1.3, 6.3], replace_all_people="true")


def test_union_includes_new_ids_after_cuts_and_keeps_empty_shots_empty(tmp_path):
    first = np.zeros((8, 8), np.uint16)
    first[1:4, 1:3] = 2
    first[2:6, 5:7] = 7
    later = np.zeros_like(first)
    later[3:6, 2:6] = 19
    frames = [first, np.zeros_like(first), later]
    source, output = tmp_path / "sam.zip", tmp_path / "all.zip"
    write_masks(source, frames)
    metadata = {"width": 8, "height": 8, "frames": 3, "scene_cuts": [1 / 24, 2 / 24]}
    assert merge_all_people_masks(source, output, metadata) == [14, 0, 12]
    with zipfile.ZipFile(output) as archive:
        for index, original in enumerate(frames):
            actual = np.array(Image.open(BytesIO(archive.read(f"labels/labels_{index:05d}.png"))))
            assert np.array_equal(actual, original > 0)
    with zipfile.ZipFile(source) as archive:
        assert np.array_equal(np.array(Image.open(BytesIO(archive.read("labels/labels_00000.png")))), first)


def test_everyone_logs_people_entering_after_the_opening_frame(tmp_path):
    first = np.zeros((8, 8), np.uint16)
    first[1:3, 1:3] = 2
    later = first.copy()
    later[4:7, 4:7] = 12
    source, output = tmp_path / "sam.zip", tmp_path / "all.zip"
    write_masks(source, [first, later, later])
    summary = {}
    merge_all_people_masks(source, output, {"width": 8, "height": 8, "frames": 3,
        "scene_cuts": [1 / 24]}, detection_summary=summary)
    assert summary == {"first_frame_people": 1, "max_people": 2, "frames_with_people": 3,
        "frames_with_multiple_people": 2, "scene_people": [
            {"start_frame": 0, "end_frame": 1, "max_people": 1},
            {"start_frame": 1, "end_frame": 3, "max_people": 2}]}


def test_empty_or_incomplete_detection_does_not_submit_a_misleading_guide(tmp_path):
    source, output = tmp_path / "sam.zip", tmp_path / "all.zip"
    metadata = {"width": 8, "height": 8, "frames": 2}
    write_masks(source, [np.zeros((8, 8), np.uint16)] * 2)
    with pytest.raises(PublicError, match="No people"):
        merge_all_people_masks(source, output, metadata)
    assert not output.exists()
    write_masks(source, [np.ones((8, 8), np.uint16)])
    with pytest.raises(PublicError, match="one mask"):
        merge_all_people_masks(source, output, metadata)
    assert not output.exists()


@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize("audio", [False, True])
def test_all_people_prompt_uses_one_reference_without_single_person_coordinates(enhanced, audio):
    payload = generation_payload(5, "image", "video", enhanced, {"x": .2, "y": .4, "time": 2},
                                 "depth", audio_processing=audio, replace_all_people=True)
    prompt = payload["prompt"]
    assert "Replace every person in every scene" in prompt
    assert "separate instance" in prompt and "individual motion" in prompt
    assert "Include people entering later scenes" in prompt
    assert "Keep all other people unchanged" not in prompt and "20%" not in prompt
    assert ("colored depth imagery" in prompt) is enhanced
    assert ("three semitones" in prompt) is audio
    assert payload["reference_image_urls"] == ["image"]
    assert payload["duration"] == 5 and payload["resolution"] == "768P"
