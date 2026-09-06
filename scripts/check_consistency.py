#!/usr/bin/env python3
"""Check skill metadata, local references, and the runtime command contract."""

import ast
from pathlib import Path
import re
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    errors = []
    for entry in sorted((root / "skills").glob("*/SKILL.md")):
        content = entry.read_text()
        frontmatter = re.match(r"\A---\n(.*?)\n---\n", content, re.S)
        if not frontmatter:
            errors.append(str(entry) + ": missing frontmatter")
            continue
        fields = dict(line.split(":", 1) for line in frontmatter[1].splitlines() if ":" in line)
        if fields.get("name", "").strip() != entry.parent.name or not fields.get("description", "").strip():
            errors.append(str(entry) + ": invalid name/description")
    for doc in root.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                errors.append(str(doc.relative_to(root)) + ": broken link " + target)
    runtime = root / "skills/hotl-gpt/scripts/hotl.py"
    tree = ast.parse(runtime.read_text())
    choices = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "command":
                choices = set(ast.literal_eval(next(k.value for k in node.keywords if k.arg == "choices")))
    reference = (root / "skills/hotl-gpt/references/runtime.md").read_text()
    documented = set(re.findall(r"^\| ([a-z]+) \|", reference, re.M))
    if choices != documented:
        errors.append("CLI/reference command mismatch: " + str(choices ^ documented))
    for error in errors:
        print("FAIL " + error)
    print("Consistency: " + (str(len(errors)) + " failures" if errors else "PASS"))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
