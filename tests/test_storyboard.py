import copy
import json

import pytest

from pipeline.common import DEFAULT_STORYBOARD, StoryboardError, load_storyboard, stable_hash


def write(tmp_path, data):
    p = tmp_path / "sb.json"
    p.write_text(json.dumps(data))
    return p


def base():
    return json.loads(DEFAULT_STORYBOARD.read_text())


def test_repo_storyboard_is_valid():
    load_storyboard()


def test_rejects_too_few_shots(tmp_path):
    d = base(); d["shots"] = d["shots"][:5]
    with pytest.raises(StoryboardError, match="shots"):
        load_storyboard(write(tmp_path, d))


def test_real_person_needs_consent(tmp_path):
    d = base(); d["characters"][0]["real_person"] = True
    d["characters"][0].pop("consent_ref", None)
    with pytest.raises(StoryboardError, match="consent_ref"):
        load_storyboard(write(tmp_path, d))


def test_end_card_must_say_ai(tmp_path):
    d = base(); d["end_card"]["lines"] = ["For grandmother"]
    with pytest.raises(StoryboardError):
        load_storyboard(write(tmp_path, d))


def test_unknown_character_reference(tmp_path):
    d = base(); d["shots"][1]["characters"] = ["nobody"]
    with pytest.raises(StoryboardError, match="unknown character"):
        load_storyboard(write(tmp_path, d))


def test_duplicate_shot_ids(tmp_path):
    d = base(); d["shots"][1]["id"] = d["shots"][0]["id"]
    with pytest.raises(StoryboardError, match="duplicate"):
        load_storyboard(write(tmp_path, d))


def test_hash_is_order_independent():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})
