"""Blender layer: script safety check, template, caching, and the fallback when the model's script is unusable."""
import json

import pytest

from pipeline import blender_layer as bl
from pipeline import keyframes, narration
from pipeline.common import load_storyboard


def test_template_is_valid_and_move_is_substituted():
    code = bl.template_script("pan_right")
    bl.check_script(code)
    assert "'x': -0.12" in code and "template move: pan_right" in code
    assert bl.template_script("pull_out") != bl.template_script("push_in")


@pytest.mark.parametrize("bad", [
    "import os\nos.system('x')",
    "import subprocess",
    "import bpy, sys\nopen('/etc/passwd')\nbpy.ops.render.render\nsys.argv\nread_factory_settings",
    "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='x')\nbpy.ops.render.render\nimport sys\nsys.argv\nread_factory_settings",
    "def broken(:",
])
def test_unsafe_or_broken_scripts_are_rejected(bad):
    with pytest.raises(bl.ScriptRejected):
        bl.check_script(bad)


def _prepare(sandbox):
    sb = load_storyboard()
    narration.run(sb, only=["s04"])
    keyframes.run(sb, only=["s04"])
    return sb


def test_gemini_script_cached_and_template_fallback(sandbox, monkeypatch):
    import pipeline.common as c
    sb = _prepare(sandbox)
    monkeypatch.setenv("HS_OFFLINE", "0")
    monkeypatch.setattr(bl, "blender_version", lambda: "Blender test")
    renders = []

    def fake_render(script, image, out, seconds, fps):
        renders.append(script.read_text())
        out.write_bytes(b"mp4")

    monkeypatch.setattr(bl, "run_blender", fake_render)
    good = bl.template_script("push_in").replace("-2.35", "-2.40")
    monkeypatch.setattr(bl, "llm_text", lambda model, prompt: f"```python\n{good}```")

    bl.run(sb, only=["s04"])
    assert "-2.40" in renders[0], "the model's script is the one rendered"
    logs = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines()]
    assert [r["stage"] for r in logs if r["stage"].startswith("blender")] == ["blender_script", "blender"]
    assert read_origin() == "llm"
    assert {r["provider"] for r in logs if r["stage"] == "blender_script"} == {"cloudflare"}, "Cloudflare is the default writer"

    n = len(logs)
    bl.run(sb, only=["s04"])
    assert len(c.RUNS_LOG.read_text().splitlines()) == n, "unchanged shot: no model call, no render"

    # The model keeps returning an unsafe script: after the repair round the template move is used, logged degraded
    sb2 = json.loads(json.dumps(sb))
    sb2["shots"][3]["motion"]["prompt"] += " and slower"
    monkeypatch.setattr(bl, "llm_text", lambda model, prompt: "```python\nimport os\nos.system('x')\n```")
    bl.run(sb2, only=["s04"])
    last = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines()][-1]
    assert last["stage"] == "blender" and last["status"] == "degraded"
    assert last["params"]["script_origin"] == "template"


def read_origin():
    return json.loads((bl.common.OUTPUTS / "blender" / "manifest.json").read_text())["s04"]["script_origin"]


def test_cloudflare_text_reads_both_response_shapes(monkeypatch):
    from pipeline import providers

    class R:
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    for payload in ({"success": True, "result": {"response": "hello"}},
                    {"success": True, "result": {"choices": [{"message": {"content": "hello"}}]}}):
        monkeypatch.setattr(providers, "_post", lambda url, **kw: R(payload))
        assert providers.cloudflare_text("@cf/x", "hi") == "hello"
    monkeypatch.setattr(providers, "_post", lambda url, **kw: R({"success": True, "result": {}}))
    with pytest.raises(providers.FatalError):
        providers.cloudflare_text("@cf/x", "hi")


def test_motion_switch_chooses_the_backend(sandbox, monkeypatch):
    import sys
    from pipeline import assemble, music, run
    from pipeline import shots as shots_mod
    called = []
    monkeypatch.setattr(run.narration, "run", lambda *a, **k: None)
    monkeypatch.setattr(run.keyframes, "run", lambda *a, **k: None)
    monkeypatch.setattr(run.music, "build", lambda *a, **k: None)
    monkeypatch.setattr(run.shots, "build", lambda *a, **k: called.append("kaggle-shots"))
    monkeypatch.setattr(run.blender_layer, "run", lambda *a, **k: called.append("blender-run"))
    monkeypatch.setattr(run.blender_layer, "build_film", lambda *a, **k: called.append("blender-film"))
    monkeypatch.setattr(run.assemble, "render", lambda *a, **k: called.append("main-film"))
    for argv, expect in ((["run"], ["kaggle-shots", "main-film", "main-film"]),
                         (["run", "--motion", "blender"], ["blender-run", "blender-film"])):
        called.clear()
        monkeypatch.setattr(sys, "argv", argv)
        run.main()
        assert called == expect
    called.clear()
    monkeypatch.setenv("HS_MOTION", "blender")
    monkeypatch.setattr(sys, "argv", ["run"])
    run.main()
    assert called == ["blender-run", "blender-film"], "HS_MOTION selects the backend when no flag is given"
