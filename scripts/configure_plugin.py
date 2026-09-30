#!/usr/bin/env python3
"""Create a clean local plugin package or a mapping to a registered hosted app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from build_plugin import ROOT, runtime_files, validate_package


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def configure(files: dict[str, bytes], *, project_id: str | None = None,
              data_dir: Path | None = None, project_dir: Path | None = None,
              port: int | None = None, mcp_url: str | None = None,
              app_id: str | None = None) -> dict[str, bytes]:
    result = dict(files)
    manifest = json.loads(result["plugin.json"])
    if app_id is not None:
        if not re.fullmatch(r"plugin_asdk_app_[A-Za-z0-9_-]+", app_id):
            raise ValueError("Use the technical plugin_asdk_app_ ID of an already registered connection")
        if any(value is not None for value in (project_id, data_dir, project_dir, port, mcp_url)):
            raise ValueError("Hosted mapping and local project settings are separate package modes")
        result.pop("mcp.json", None)
        manifest["extensions"]["com.openai"]["apps"] = "./.app.json"
        result[".app.json"] = json_bytes({"apps": {"scene_feedback": {"id": app_id, "required": True}}})
    else:
        if not project_id or not re.fullmatch(r"[0-9a-f]{32}", project_id):
            raise ValueError("Local package requires the workbench's 32-character project ID")
        if data_dir is None or project_dir is None or port is None or not 1 <= port <= 65535:
            raise ValueError("Local package requires data directory, project directory, and port (1–65535)")
        data_dir = data_dir.expanduser().resolve()
        project_dir = project_dir.expanduser().resolve()
        if not data_dir.is_dir() or not project_dir.is_dir():
            raise ValueError("The configured workspace and data directories must already exist")
        env = {
            "SCENE_FEEDBACK_PROJECT_ID": project_id,
            "SCENE_FEEDBACK_DATA_DIR": str(data_dir),
            "SCENE_FEEDBACK_PROJECT_DIR": str(project_dir),
            "SCENE_FEEDBACK_PORT": str(port),
        }
        if mcp_url:
            parsed = urlsplit(mcp_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path != f"/p/{project_id}/mcp"
                    or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError("MCP URL must match this project and use HTTPS or loopback HTTP")
            env["SCENE_FEEDBACK_MCP_URL"] = mcp_url
        mcp = json.loads(result["mcp.json"])
        mcp["mcpServers"]["scene_feedback"]["env"] = env
        result["mcp.json"] = json_bytes(mcp)
    result["plugin.json"] = json_bytes(manifest)
    validate_package(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--mcp-url")
    parser.add_argument("--app-id", help="Existing hosted connection technical ID; this script does not register a connection")
    parser.add_argument("--output", type=Path, required=True, help="New, empty plugin directory outside live workspace data")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output == ROOT or output.is_relative_to(ROOT / "backend") or output.is_relative_to(ROOT / "web"):
        parser.error("Output must not overwrite repository source")
    if args.data_dir is not None and output.is_relative_to(args.data_dir.expanduser().resolve()):
        parser.error("Output must not be inside workspace data")
    if output.exists() and not output.is_dir():
        parser.error("Output must be a new or empty directory")
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty; choose a new directory for each package")
    try:
        files = configure(runtime_files(), project_id=args.project_id, data_dir=args.data_dir,
                          project_dir=args.project_dir, port=args.port,
                          mcp_url=args.mcp_url, app_id=args.app_id)
    except ValueError as exc:
        parser.error(str(exc))
    output.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    mode = "hosted connection mapping" if args.app_id else "local project bridge"
    print(f"Configured {output} ({mode}; no credentials copied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
