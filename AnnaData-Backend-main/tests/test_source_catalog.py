import json
from pathlib import Path

import pytest

from source_catalog import assert_trusted_url, load_catalog, scope_score


MANIFEST = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"


def _manifest_with(tmp_path, **changes):
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows[0].update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_initial_catalog_contains_each_approved_domain():
    catalog = load_catalog(MANIFEST)
    assert set(catalog) == {
        "pm_kisan_guidelines",
        "pmfby_2023_guidelines",
        "kcc_2026_overview",
        "soil_health_card_faq",
        "enam_operational_guidelines",
        "nhb_cold_chain_standards_2025",
        "icar_crop_management_2023_24",
        "pau_kharif_2025",
        "pau_rabi_2025_26",
    }
    assert {item.tier for item in catalog.values()} == {"official", "extension"}
    assert all(item.required_terms for item in catalog.values())


def test_unlisted_host_is_rejected():
    with pytest.raises(ValueError, match="untrusted source host"):
        assert_trusted_url("https://example.com/fertilizer-advice.pdf")


def test_absolute_local_path_is_rejected(tmp_path):
    manifest = _manifest_with(tmp_path, local_path="/tmp/replaced.pdf")

    with pytest.raises(ValueError, match="local path"):
        load_catalog(manifest)


def test_parent_traversal_in_local_path_is_rejected(tmp_path):
    manifest = _manifest_with(tmp_path, local_path="data/downloads/../replaced.pdf")

    with pytest.raises(ValueError, match="local path"):
        load_catalog(manifest)


def test_unknown_fetch_mode_is_rejected(tmp_path):
    manifest = _manifest_with(tmp_path, fetch_mode="ftp")

    with pytest.raises(ValueError, match="fetch mode"):
        load_catalog(manifest)


def test_extension_scope_matches_only_its_declared_state_and_crop():
    scope = {"states": ["Punjab"], "crops": ["wheat", "mustard"]}
    assert scope_score(scope, "Punjab", "wheat") == 4
    assert scope_score(scope, "Punjab", "rice") is None
    assert scope_score(scope, "West Bengal", "wheat") is None
