#!/usr/bin/env python3
"""Install repository-scoped skills without touching global Codex configuration."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


def install(target, pm=False, link=False, force=False):
    root = Path(__file__).resolve().parents[1]
    target = Path(target).resolve()
    if not target.is_dir():
        raise ValueError("Target directory must already exist")
    base = target / ".agents" / "skills"
    backups = target / ".agents" / "hotl-backups"
    for directory in (target / ".agents", base, backups):
        if directory.is_symlink():
            raise ValueError("Install parent must not be a symlink: " + str(directory))
    names = ["hotl-gpt"] + (["hotl-gpt-pm"] if pm else [])
    for name in names:
        destination = base / name
        if os.path.lexists(destination) and not force:
            if link and destination.is_symlink() and destination.resolve() == root / "skills" / name:
                continue
            raise ValueError("Already installed: %s. Use --force to replace it with a backup." % destination)
    base.mkdir(parents=True, exist_ok=True)
    results = []
    for name in names:
        source, destination = root / "skills" / name, base / name
        if link and destination.is_symlink() and destination.resolve() == source:
            results.append({"skill": name, "status": "already linked", "path": str(destination)})
            continue
        stage = Path(tempfile.mkdtemp(prefix=".hotl-install-", dir=str(base)))
        backup = None
        try:
            candidate = stage / name
            if link:
                candidate.symlink_to(source, target_is_directory=True)
            else:
                shutil.copytree(source, candidate, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if os.path.lexists(destination):
                backups.mkdir(exist_ok=True)
                backup = backups / (name + "-" + uuid.uuid4().hex)
                destination.rename(backup)
            try:
                candidate.rename(destination)
            except OSError:
                if backup is not None:
                    backup.rename(destination)
                raise
            results.append({"skill": name, "status": "linked" if link else "copied",
                            "path": str(destination), "backup": str(backup) if backup else None})
        finally:
            shutil.rmtree(stage)
    return {"version": (root / "VERSION").read_text().strip(), "installed": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--pm", action="store_true")
    parser.add_argument("--link", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.target, args.pm, args.link, args.force), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        print("install: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
