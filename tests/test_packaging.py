"""Tests for building the HACS integration release archive."""

import json
import zipfile
from io import BytesIO
from uuid import uuid4

import pytest

from scripts.package_release import (
    ARCHIVE_NAME,
    INTEGRATION_DIR,
    REQUIRED_PACKAGE_FILES,
    _validate_icon,
    build_release_archive,
)

EXPECTED_PACKAGE_FILES = {
    "__init__.py",
    "client.py",
    "config_flow.py",
    "const.py",
    "media_player.py",
    "select.py",
    "manifest.json",
    "strings.json",
    "translations/en.json",
    "brand/icon.png",
    "LICENSE",
    "README.md",
}


def test_hacs_manifest_targets_built_archive() -> None:
    """Keep the HACS release asset name aligned with the package builder."""
    manifest_path = INTEGRATION_DIR.parents[1] / "hacs.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["zip_release"] is True
    assert manifest["filename"] == ARCHIVE_NAME
    assert manifest["hide_default_branch"] is True


def test_release_archive_has_hacs_layout_and_hydrated_icon() -> None:
    """HACS extracts the archive into the integration directory itself."""
    assert set(REQUIRED_PACKAGE_FILES) == EXPECTED_PACKAGE_FILES
    archive_bytes = build_release_archive(INTEGRATION_DIR)

    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        names = set(archive.namelist())
        assert names == EXPECTED_PACKAGE_FILES
        integration_modules = {
            path.relative_to(INTEGRATION_DIR).as_posix()
            for path in INTEGRATION_DIR.rglob("*.py")
        }
        assert integration_modules <= names
        assert not any(name.startswith("custom_components/") for name in names)
        assert archive.read("brand/icon.png").startswith(b"\x89PNG\r\n\x1a\n")
        project_root = INTEGRATION_DIR.parents[1]
        assert archive.read("LICENSE") == (project_root / "LICENSE").read_bytes()
        license_text = archive.read("LICENSE").decode("utf-8")
        readme_text = " ".join(archive.read("README.md").decode("utf-8").split())
        assert "GNU GENERAL PUBLIC LICENSE" in license_text
        assert "Version 3, 29 June 2007" in license_text
        assert "END OF TERMS AND CONDITIONS" in license_text
        assert "or (at your option) any later version (GPL-3.0-or-later)" in readme_text


def test_release_archive_excludes_incidental_local_files() -> None:
    """Keep local secrets, hidden files, and backups out of release archives."""
    suffix = uuid4().hex
    incidental_paths = (
        INTEGRATION_DIR / f".env-{suffix}",
        INTEGRATION_DIR / f"client.py.bak-{suffix}",
        INTEGRATION_DIR / "translations" / f"local-secret-{suffix}.json",
    )
    try:
        for path in incidental_paths:
            path.write_text("test-only sentinel", encoding="utf-8")

        with zipfile.ZipFile(
            BytesIO(build_release_archive(INTEGRATION_DIR))
        ) as archive:
            assert set(archive.namelist()) == EXPECTED_PACKAGE_FILES
    finally:
        for path in incidental_paths:
            path.unlink(missing_ok=True)


def test_release_archive_fails_when_required_files_are_missing() -> None:
    """Report required files explicitly instead of building a partial archive."""
    missing_directory = INTEGRATION_DIR / f"missing-{uuid4().hex}"
    with pytest.raises(ValueError, match="Required integration package file"):
        build_release_archive(missing_directory)

    missing_project_root = INTEGRATION_DIR / f"missing-project-{uuid4().hex}"
    with pytest.raises(ValueError, match="LICENSE, README.md"):
        build_release_archive(INTEGRATION_DIR, project_root=missing_project_root)


def test_release_archive_rejects_lfs_pointer() -> None:
    """Do not publish an archive containing an unresolved Git LFS pointer."""
    with pytest.raises(ValueError, match="LFS pointer"):
        _validate_icon(b"version https://git-lfs.github.com/spec/v1\n")
