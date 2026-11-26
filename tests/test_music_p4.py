"""Unit tests for P4/P5: music .flac export and valid Kaggle notebook JSON."""
import json
from pathlib import Path

from pipeline import music
from pipeline.common import ROOT, load_storyboard


def test_music_export_outputs_flac(sandbox, monkeypatch):
    monkeypatch.setattr(music, "JOB_DIR", sandbox / "kaggle_music_job")
    sb = load_storyboard()
    music.export(sb)
    jobs = json.loads((sandbox / "kaggle_music_job" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert len(jobs) == sb["music"]["candidates"]
    assert all(j["output"].endswith(".flac") for j in jobs)


def test_kaggle_notebooks_valid_json():
    for rel in ("notebooks/kaggle_music/acestep_score.ipynb", "notebooks/kaggle_wan/wan22_i2v.ipynb"):
        nb = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        assert nb["nbformat"] == 4 and len(nb["cells"]) >= 4
