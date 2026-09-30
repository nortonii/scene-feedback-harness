#!/usr/bin/env python3
"""Build a reproducible plugin ZIP from an explicit runtime file allowlist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
NAME = "scene-feedback-harness"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
FIXED_FILES = (
    "plugin.json", "mcp.json", "LICENSE", "README.md", "README.en.md",
    "README.ja.md", "README.ko.md", "README.ru.md", "CHANGELOG.md", "CONTRIBUTING.md", "preview.png",
    "docs/mcp-events-plugin.md", "scripts/plugin_bridge.py",
    "scripts/run_pose_worker.py", "scripts/build_plugin.py",
    "scripts/configure_plugin.py", ".agents/plugins/marketplace.json",
)


def runtime_files(root: Path = ROOT) -> dict[str, bytes]:
    root = root.resolve()
    selected = set(FIXED_FILES)
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    if manifest.get("extensions", {}).get("com.openai", {}).get("apps"):
        selected.discard("mcp.json")
        selected.add(".app.json")
    selected.update(str(path.relative_to(root)) for path in (root / "backend").glob("*.py"))
    selected.update(str(path.relative_to(root)) for path in (root / "backend").glob("requirements*.txt"))
    for directory in ("web", "skills"):
        selected.update(str(path.relative_to(root)) for path in (root / directory).rglob("*") if path.is_file())
    result = {}
    for relative in sorted(selected):
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Plugin file is missing or is not a contained regular file: {relative}")
        # Reject directory symlinks too: a runtime package must contain its own bytes.
        if any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError(f"Plugin file traverses a symlink: {relative}")
        result[relative] = path.read_bytes()
    validate_package(result)
    return result


def validate_package(files: dict[str, bytes]) -> None:
    manifest = json.loads(files["plugin.json"])
    if manifest.get("$schema") != PLUGIN_SCHEMA or manifest.get("name") != NAME:
        raise ValueError("Plugin identity or portable schema is invalid")
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest.get("version", "")):
        raise ValueError("Plugin version must be a semantic release version")
    if "skills/visual-feedback/SKILL.md" not in files or "scripts/plugin_bridge.py" not in files:
        raise ValueError("Plugin is missing its review skill or bridge")
    extension = manifest.get("extensions", {}).get("com.openai", {})
    if extension.get("apps"):
        if extension["apps"] != "./.app.json" or ".app.json" not in files:
            raise ValueError("Hosted mapping must use the contained .app.json")
        apps = json.loads(files[".app.json"]).get("apps", {})
        app_id = apps.get("scene_feedback", {}).get("id", "")
        if not re.fullmatch(r"plugin_asdk_app_[A-Za-z0-9_-]+", app_id):
            raise ValueError("Hosted mapping requires a registered plugin_asdk_app ID")
        if "mcp.json" in files:
            raise ValueError("Hosted package must not also connect the local bridge")
    else:
        mcp = json.loads(files["mcp.json"])
        server = mcp.get("mcpServers", {}).get("scene_feedback", {})
        if (mcp.get("$schema") != MCP_SCHEMA or server.get("type") != "stdio"
                or server.get("command") != "python3"
                or server.get("args") != ["${PLUGIN_ROOT}/scripts/plugin_bridge.py"]
                or server.get("cwd") != "${PLUGIN_ROOT}"):
            raise ValueError("Local plugin must use its contained stdio bridge")
    for name in files:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or any(part in {".venv", ".git", "__pycache__", "data", "tests"} for part in path.parts):
            raise ValueError(f"Disallowed package path: {name}")
        if path.name in {"control_token", "auth.json", "connection.json"}:
            raise ValueError(f"Secrets and workspace connection state cannot be bundled: {name}")
        if path.name == ".env" or path.name.startswith(".env.") or path.suffix in {".pem", ".key", ".pth", ".pt", ".safetensors"}:
            raise ValueError(f"Credentials and model weights cannot be bundled: {name}")


def write_zip(files: dict[str, bytes], output: Path) -> None:
    validate_package(files)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in sorted(files.items()):
            info = zipfile.ZipInfo(f"{NAME}/{relative}", date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT, help="Repository or configured plugin directory")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"{NAME}.zip")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.suffix != ".zip" or any(output.is_relative_to(source / directory) for directory in ("backend", "web", "skills")):
        parser.error("Choose a .zip output outside runtime source directories")
    files = runtime_files(args.source)
    write_zip(files, output)
    print(f"Built {output} ({len(files)} runtime files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
