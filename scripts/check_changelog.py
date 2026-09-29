#!/usr/bin/env python3
"""Require a new changelog entry in each staged update or committed update."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys


ENTRY = re.compile(r"^### (?:[01]\d|2[0-3]):[0-5]\d · \S.*$")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], text=True, encoding="utf-8", capture_output=True, check=check)


def has_entry(patch: str) -> bool:
    added = [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]
    grows = sum(bool(ENTRY.fullmatch(line)) for line in added) > sum(bool(ENTRY.fullmatch(line)) for line in removed)
    return grows and any(line.startswith("- ") and line[2:].strip() for line in added)


def staged() -> list[str]:
    if not git("diff", "--cached", "--name-only", "--no-renames").stdout.strip():
        return []
    patch = git("diff", "--cached", "--no-ext-diff", "--unified=0", "--", "CHANGELOG.md").stdout
    return [] if has_entry(patch) else ["暂存的更新没有新的日志条目：请在 CHANGELOG.md 添加本次标题与说明，并一起 git add。"]


def resolve(revision: str) -> str:
    return git("rev-parse", "--verify", "--end-of-options", revision + "^{commit}").stdout.strip()


def committed(base: str, head: str) -> list[str]:
    end = resolve(head)
    interval = end if not base or set(base) == {"0"} else resolve(base) + ".." + end
    commits = git("rev-list", "--reverse", "--no-merges", interval, "--").stdout.splitlines()
    errors = []
    for commit in commits:
        parents = git("rev-list", "--parents", "-n", "1", commit).stdout.split()[1:]
        parent = parents[0] if parents else None
        existed = bool(parent and git("cat-file", "-e", parent + ":CHANGELOG.md", check=False).returncode == 0)
        exists = git("cat-file", "-e", commit + ":CHANGELOG.md", check=False).returncode == 0
        # Old commits are documented by the initial backfill, not rewritten.
        if not existed and not exists:
            continue
        if parent:
            changed = git("diff", "--name-only", parent, commit, "--").stdout.strip()
            patch = git("diff", "--no-ext-diff", "--unified=0", parent, commit, "--", "CHANGELOG.md").stdout
        else:
            changed = git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit).stdout.strip()
            patch = git("show", "--format=", "--no-ext-diff", "--unified=0", commit, "--", "CHANGELOG.md").stdout
        if changed and not has_entry(patch):
            subject = git("show", "-s", "--format=%s", commit).stdout.strip()
            errors.append(f"{commit[:7]} {subject}：缺少随本次更新新增的 CHANGELOG.md 标题与说明。")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--staged", action="store_true")
    modes.add_argument("--range", nargs=2, metavar=("BASE_SHA", "HEAD_SHA"))
    args = parser.parse_args()
    try:
        errors = staged() if args.staged else committed(*args.range)
    except subprocess.CalledProcessError as exc:
        print("无法检查 Git 更新日志：" + exc.stderr.strip(), file=sys.stderr)
        return 2
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("更新日志检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
