# SPDX-License-Identifier: GPL-2.0-or-later
"""Syntax-check a workflow: YAML shape (duplicate keys rejected), embedded github-script JS, bash run steps.

A workflow_call workflow must also give every job a permissions block (the caller
grants a superset) and declare every secret it reads.

Usage: python check_workflow.py <workflow.yml>
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

EXPR = re.compile(r"\$\{\{[^}]*\}\}")
SECRET = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")


class _Loader(yaml.SafeLoader):
    """Rejects duplicate mapping keys, as the Actions parser does."""


def _mapping(loader, node, deep=False):
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    dup = sorted({str(k) for k in keys if keys.count(k) > 1})
    if dup:
        raise yaml.YAMLError(f"duplicate key(s) {dup} at line {node.start_mark.line + 1}")
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def check_js(name: str, script: str) -> list[str]:
    src = EXPR.sub("EXPR", script)
    wrapped = "async function main(github, context, core, require, process) {\n" + src + "\n}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(wrapped)
        tmp = f.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, encoding="utf-8")
    finally:
        os.unlink(tmp)
    return [f"{name}: {r.stderr.strip()}"] if r.returncode else []


def check_bash(name: str, script: str) -> list[str]:
    src = EXPR.sub("EXPR", script)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        r = subprocess.run(["bash", "-n", tmp], capture_output=True, text=True, encoding="utf-8")
    finally:
        os.unlink(tmp)
    return [f"{name}: {r.stderr.strip()}"] if r.returncode else []


def main(path: str) -> int:
    doc = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_Loader)
    problems: list[str] = []
    jobs = doc.get("jobs", {})
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = doc.get("on", doc.get(True)) or {}
    if isinstance(triggers, dict) and "workflow_call" in triggers:
        declared = set(((triggers.get("workflow_call") or {}).get("secrets") or {}).keys()) | {"GITHUB_TOKEN"}
        used = set(SECRET.findall(Path(path).read_text(encoding="utf-8")))
        for name in sorted(used - declared):
            problems.append(f"secrets.{name} is read but not declared under on.workflow_call.secrets")
        for job, jd in jobs.items():
            if "permissions" not in jd:
                problems.append(f"{job}: no permissions block in a workflow_call workflow")
    n_js = n_sh = 0
    for job, jd in jobs.items():
        for i, step in enumerate(jd.get("steps", [])):
            name = f"{job}[{i}] {step.get('name', step.get('id', ''))}"
            uses = step.get("uses", "")
            if uses.startswith("actions/github-script") and "script" in (step.get("with") or {}):
                n_js += 1
                problems += check_js(name, step["with"]["script"])
            if "run" in step:
                n_sh += 1
                problems += check_bash(name, step["run"])
            if uses.startswith("tj-actions/"):
                problems.append(f"{name}: uses {uses} (mutable third-party tag)")
    for p in problems:
        print("PROBLEM", p)
    print(f"{path}: {len(jobs)} jobs, {n_js} github-script steps, {n_sh} run steps, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
