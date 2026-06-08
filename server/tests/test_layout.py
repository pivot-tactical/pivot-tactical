"""Tests for the side-by-side install layout (spec §3.7.5)."""

from __future__ import annotations

import pytest

from pivot.updates.layout import Layout


def _bundle(tmp_path, name="bundle"):
    src = tmp_path / name
    src.mkdir()
    (src / "PIVOT-Tactical.exe").write_text("binary")
    return src


def test_place_version_moves_bundle_into_app_dir(tmp_path):
    layout = Layout(tmp_path / "versions")
    bundle = _bundle(tmp_path, "extracted-1.0.0")

    dest = layout.place_version("1.0.0", bundle)

    assert dest == layout.app_dir("1.0.0")
    assert (dest / "PIVOT-Tactical.exe").read_text() == "binary"
    assert not bundle.exists()  # moved, not copied
    assert layout.installed_versions() == ["1.0.0"]


def test_place_version_replaces_existing_dir(tmp_path):
    layout = Layout(tmp_path / "versions")
    layout.place_version("1.0.0", _bundle(tmp_path, "first"))
    layout.place_version("1.0.0", _bundle(tmp_path, "second"))

    assert layout.installed_versions() == ["1.0.0"]
    assert (layout.app_dir("1.0.0") / "PIVOT-Tactical.exe").exists()


def test_activate_creates_link_and_resolves_active_version(tmp_path):
    layout = Layout(tmp_path / "versions")
    layout.place_version("1.0.0", _bundle(tmp_path, "a"))
    layout.place_version("1.1.0", _bundle(tmp_path, "b"))

    assert layout.active_version() is None  # no link yet

    layout.activate("1.0.0")
    assert layout.active_version() == "1.0.0"
    assert layout.current_exe("PIVOT-Tactical.exe").read_text() == "binary"

    # Re-activating a different version flips the link atomically.
    layout.activate("1.1.0")
    assert layout.active_version() == "1.1.0"


def test_activate_rejects_uninstalled_version(tmp_path):
    layout = Layout(tmp_path / "versions")
    with pytest.raises(ValueError, match="Version not installed"):
        layout.activate("9.9.9")


def test_installed_versions_sorted_newest_first(tmp_path):
    layout = Layout(tmp_path / "versions")
    for tag in ["1.0.0", "2.0.0", "1.5.0"]:
        layout.place_version(tag, _bundle(tmp_path, f"b-{tag}"))

    assert layout.installed_versions() == ["2.0.0", "1.5.0", "1.0.0"]


def test_delete_version_refuses_active_and_unknown(tmp_path):
    layout = Layout(tmp_path / "versions")
    layout.place_version("1.0.0", _bundle(tmp_path, "a"))
    layout.place_version("1.1.0", _bundle(tmp_path, "b"))
    layout.activate("1.0.0")

    assert layout.delete_version("1.0.0") is False  # active
    assert layout.delete_version("9.9.9") is False  # unknown
    assert layout.delete_version("1.1.0") is True
    assert layout.installed_versions() == ["1.0.0"]


def test_prune_keeps_newest_active_and_protected(tmp_path):
    layout = Layout(tmp_path / "versions")
    for tag in ["1.0.0", "1.1.0", "1.2.0", "1.3.0"]:
        layout.place_version(tag, _bundle(tmp_path, f"b-{tag}"))
    layout.activate("1.0.0")  # active is older than the kept window

    layout.prune(keep=2, protect=("1.2.0",))

    kept = set(layout.installed_versions())
    assert kept == {"1.3.0", "1.2.0", "1.0.0"}
    assert "1.1.0" not in kept
