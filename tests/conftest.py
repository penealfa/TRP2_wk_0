import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point outputs and runs.jsonl at a temp dir and switch to placeholder generators."""
    monkeypatch.setenv("HS_OFFLINE", "1")
    import pipeline.common as c
    monkeypatch.setattr(c, "OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(c, "RUNS_LOG", tmp_path / "runs.jsonl")
    for mod in ("pipeline.narration", "pipeline.keyframes", "pipeline.shots", "pipeline.music", "pipeline.assemble", "pipeline.blender_layer"):
        m = __import__(mod, fromlist=["x"])
        for name in ("OUTPUTS", "RUNS_LOG"):
            if hasattr(m, name):
                monkeypatch.setattr(m, name, getattr(c, name))
    import pipeline.narration as n
    monkeypatch.setattr(n, "RECORDINGS", tmp_path / "recordings")
    (tmp_path / "recordings").mkdir()
    import pipeline.keyframes as kf
    monkeypatch.setattr(kf, "ASSETS_REFS", tmp_path / "assets" / "refs")
    (tmp_path / "assets" / "refs").mkdir(parents=True)
    return tmp_path
