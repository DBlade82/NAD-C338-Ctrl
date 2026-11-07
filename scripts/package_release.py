"""Build the HACS release archive for the NAD C 338 integration."""

from __future__ import annotations

import argparse
import json
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = ROOT / "custom_components" / "nad_c338"
ARCHIVE_NAME = "nad-c338.zip"
DEFAULT_OUTPUT = ROOT / "dist" / ARCHIVE_NAME
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
INTEGRATION_FILES = (
    "__init__.py",
    "client.py",
    "config_flow.py",
    "const.py",
    "media_player.py",
    "manifest.json",
    "strings.json",
    "translations/en.json",
    "brand/icon.png",
)
PROJECT_FILES = ("LICENSE", "README.md")
REQUIRED_PACKAGE_FILES = INTEGRATION_FILES + PROJECT_FILES


def build_release_archive(integration_dir: Path, project_root: Path = ROOT) -> bytes:
    """Build an integration-root ZIP after validating its release metadata and icon.

    Args:
        integration_dir: Directory containing the integration files.
        project_root: Directory containing the project license and readme.

    Returns:
        ZIP archive bytes with integration files at the archive root.

    Raises:
        ValueError: If manifest metadata or the hydrated brand icon is invalid.
    """
    package_sources = {
        **{
            relative_path: integration_dir / relative_path
            for relative_path in INTEGRATION_FILES
        },
        **{
            relative_path: project_root / relative_path
            for relative_path in PROJECT_FILES
        },
    }
    missing = [
        relative_path
        for relative_path, path in package_sources.items()
        if not path.is_file()
    ]
    if missing:
        raise ValueError(
            f"Required integration package file(s) missing: {', '.join(missing)}"
        )

    manifest_path = integration_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("domain") != "nad_c338" or not manifest.get("version"):
        raise ValueError("Integration manifest must define domain and version.")

    _validate_icon((integration_dir / "brand" / "icon.png").read_bytes())

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, path in package_sources.items():
            archive.write(path, relative_path)
    return buffer.getvalue()


def _validate_icon(icon: bytes) -> None:
    """Reject unresolved LFS pointers and non-PNG brand assets."""
    if icon.startswith(LFS_POINTER_PREFIX):
        raise ValueError(
            "Brand icon is an LFS pointer; run `git lfs pull` before packaging."
        )
    if not icon.startswith(PNG_SIGNATURE):
        raise ValueError("Brand icon is not a valid PNG.")


def main() -> None:
    """Build the HACS archive at the requested output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path (default: dist/nad-c338.zip)",
    )
    args = parser.parse_args()

    archive = build_release_archive(INTEGRATION_DIR)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(archive)
    version = json.loads(
        (INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8")
    )["version"]
    print(f"Built {args.output} for version {version}")


if __name__ == "__main__":
    main()
