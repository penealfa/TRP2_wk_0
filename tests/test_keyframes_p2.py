"""Unit tests for P2: per-character reference sheets, FLUX.2 references, contact sheet, degraded fallback."""
import copy
import io
import json

from PIL import Image

from pipeline import keyframes
from pipeline.common import FatalError, load_storyboard


def _png_bytes(color=(120, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


def test_flux2_reference_counts_and_wolf_invalidation(sandbox, monkeypatch):
    import pipeline.common as c
    monkeypatch.setenv("HS_OFFLINE", "0")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")

    recorded_calls = []

    def fake_cf(model, prompt, *, seed, width, height, steps=None, reference_images=None):
        refs = list(reference_images or [])
        recorded_calls.append((seed, len(refs), prompt))
        return _png_bytes((seed % 200, 90, 50))

    monkeypatch.setattr(keyframes, "cloudflare_image", fake_cf)

    sb = load_storyboard()
    keyframes.run(sb)

    by_seed = {seed: n_refs for seed, n_refs, _ in recorded_calls}
    assert by_seed[108] == 2, "s08 has ['peneal', 'wolf'] -> must pass 2 reference images"
    assert by_seed[107] == 0, "s07 has [] -> must pass 0 reference images"
    assert (c.OUTPUTS / "keyframes" / "contact_sheet.png").exists()

    first_log_len = len(c.RUNS_LOG.read_text().splitlines())

    # Editing wolf description must regenerate ref_wolf plus s02, s04, s05, s06, s08, s11 only
    sb2 = copy.deepcopy(sb)
    sb2["characters"][1]["description"] += ", darker amber markings"
    keyframes.run(sb2)

    new_logs = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines()][first_log_len:]
    assert sorted(r["item"] for r in new_logs) == ["ref_wolf", "s02", "s04", "s05", "s06", "s08", "s11"]


def test_user_photo_ref_and_candidates_in_contact_sheet(sandbox, monkeypatch):
    import pipeline.common as c
    monkeypatch.setenv("HS_OFFLINE", "0")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")

    (sandbox / "assets" / "refs" / "peneal.png").write_bytes(_png_bytes((10, 20, 30)))
    seen = []

    def fake_cf(model, prompt, *, seed, width, height, steps=None, reference_images=None):
        seen.append((seed, len(reference_images or [])))
        return _png_bytes((seed % 200, 100, 60))

    monkeypatch.setattr(keyframes, "cloudflare_image", fake_cf)

    sb = load_storyboard()
    keyframes.run(sb, only=["s01"], candidates=2)
    by_seed = {seed: n_refs for seed, n_refs in seen}
    assert by_seed[2611] == 1, "ref_peneal must receive assets/refs/peneal.png"
    assert by_seed[2612] == 0, "ref_wolf has no user photo"
    assert (c.OUTPUTS / "keyframes" / "contact_sheet.png").exists()
    cand_files = list((c.OUTPUTS / "keyframes").glob("s01_cand1__*.png"))
    assert len(cand_files) == 1


def test_degraded_fallback_when_image_fields_rejected(sandbox, monkeypatch):
    import pipeline.common as c
    monkeypatch.setenv("HS_OFFLINE", "0")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")

    def fake_cf(model, prompt, *, seed, width, height, steps=None, reference_images=None):
        if reference_images:
            raise FatalError("Workers AI error: input_image_0 field not supported")
        return _png_bytes((50, 60, 70))

    monkeypatch.setattr(keyframes, "cloudflare_image", fake_cf)
    sb = load_storyboard()
    keyframes.run(sb, only=["s01"])

    rows = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines()]
    degraded = [r for r in rows if r["item"] == "s01" and r["status"] == "degraded"]
    assert len(degraded) == 1
    assert degraded[0]["params"]["refs"] == 0


def test_flagged_output_retries_next_seed_without_text_only_fallback(sandbox, monkeypatch):
    import pipeline.common as c
    monkeypatch.setenv("HS_OFFLINE", "0")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    calls = []

    def fake_cf(model, prompt, *, seed, width, height, steps=None, reference_images=None):
        calls.append((seed, len(reference_images or [])))
        if seed == 101:  # s01's storyboard seed: flagged once
            raise FatalError('HTTP 400: {"errors":[{"message":"AiError: Your output has been flagged. '
                             'Please choose another prompt / input image combination","code":3030}]}')
        return _png_bytes((seed % 200, 90, 50))

    monkeypatch.setattr(keyframes, "cloudflare_image", fake_cf)
    keyframes.run(load_storyboard(), only=["s01"])

    s01_calls = [cl for cl in calls if cl[0] in (101, 102)]
    assert s01_calls == [(101, 1), (102, 1)], "flag -> next seed, references kept (no text-only fallback)"
    logs = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines() if '"s01"' in l]
    assert [r["status"] for r in logs] == ["fatal_error", "ok"]
    assert logs[-1]["seed"] == 102
