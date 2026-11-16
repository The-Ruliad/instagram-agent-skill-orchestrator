#!/usr/bin/env python3
"""Add this pack to TR_OS's configuration. Idempotent; prints what it changed.

  skills/sets.json                an "instagram" set holding every skill in this
                                  repo, linked into each --project
  ~/.tr_os/permission_policy.json a deploy pattern that gates
                                  `igapi.py ... publish ... --confirm` as instagram_publish

Run by tros/install.sh, which the operator runs: sessions cannot write either file.

  python3 tros/configure.py --sets FILE --policy FILE --skills a,b,c --project DIR [--project DIR]
"""

import argparse
import json
import os
import re

SET_NAME = "instagram"
GATE = {
    "name": "instagram_publish",
    "regex": r"\bigapi\.py\b.*\bpublish\b.*\s--confirm\b",
    "label": "Instagram post to a live account (igapi.py publish --confirm)",
    "why": "a post on a client's Instagram is public; the dry run and every read stay ungated",
}
WIDTH = 104


def inline(value):
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def dump(obj, level=0):
    """sets.json's own layout: two-space indent, short lists and small objects on
    one line, long string lists wrapped, so a re-dump of an unchanged file is
    byte-identical and an edit shows up as an edit."""
    pad = "  " * level
    if isinstance(obj, dict):
        flat = inline(obj)
        if level > 1 and len(flat) <= 60 and all(isinstance(v, str) for v in obj.values()):
            return flat
        rows = [f"{pad}  {json.dumps(k, ensure_ascii=False)}: {dump(v, level + 1)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"
    if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
        flat = inline(obj)
        if len(pad) + len(flat) <= 72:
            return flat
        lines, current = [], []
        for item in obj:
            piece = json.dumps(item, ensure_ascii=False)
            if current and len(pad) + 2 + len(", ".join(current + [piece])) + 1 > WIDTH:
                lines.append(", ".join(current) + ",")
                current = []
            current.append(piece)
        lines.append(", ".join(current))
        return "[\n" + "\n".join(f"{pad}  {line}" for line in lines) + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)


def edit_in_place(text, data):
    """Apply `data`'s instagram set and project lists to sets.json's text without
    touching any other line, so the operator's diff shows only this change.
    Returns None when the file's shape is not the one expected."""
    skills_text = dump(data["sets"][SET_NAME], 2)
    entry = re.search(rf'\n    "{SET_NAME}": (\[[^\]]*\])', text)
    if entry:
        text = text[:entry.start(1)] + skills_text + text[entry.end(1):]
    else:
        end_of_sets = re.search(r'\n  \},\n  "user_skills"', text)
        if not end_of_sets:
            return None
        text = (text[:end_of_sets.start()] + f',\n    "{SET_NAME}": {skills_text}' + text[end_of_sets.start():])
    for project, sets in data["projects"].items():
        line = re.search(rf'\n    {re.escape(json.dumps(project, ensure_ascii=False))}: (\[[^\]\n]*\])', text)
        if line:
            text = text[:line.start(1)] + inline(sets) + text[line.end(1):]
            continue
        end_of_projects = re.search(r'\n  \}(,\n  "[^"]+": |\n\}\s*$)', text[text.find('"projects"'):])
        if not end_of_projects:
            return None
        at = text.find('"projects"') + end_of_projects.start()
        text = text[:at] + f",\n    {json.dumps(project, ensure_ascii=False)}: {inline(sets)}" + text[at:]
    try:
        return text if json.loads(text) == data else None
    except ValueError:
        return None


def configure_sets(path, skills, projects):
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    data = json.loads(original)
    changes = []
    if data["sets"].get(SET_NAME) != skills:
        data["sets"][SET_NAME] = skills
        changes.append(f"set {SET_NAME}: {len(skills)} skills")
    for project in projects:
        current = data["projects"].setdefault(project, [])
        if SET_NAME not in current:
            current.append(SET_NAME)
            changes.append(f"project {project}: + {SET_NAME}")
    if changes:
        text = edit_in_place(original, data) or dump(data) + "\n"
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
    return changes


def configure_policy(path):
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    patterns = data.setdefault("deploy_patterns", [])
    for entry in patterns:
        if isinstance(entry, dict) and entry.get("name") == GATE["name"]:
            if entry.get("regex") == GATE["regex"]:
                return []
            entry.update(GATE)
            break
    else:
        patterns.append(dict(GATE))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(path + ".tmp", path)
    return [f"gate {GATE['name']}: {GATE['regex']}"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--skills", required=True, help="comma-separated skill names")
    ap.add_argument("--project", action="append", default=[])
    args = ap.parse_args()
    skills = [s for s in args.skills.split(",") if s]
    projects = [os.path.abspath(os.path.expanduser(p)) for p in args.project]
    for change in configure_sets(args.sets, skills, projects) + configure_policy(args.policy):
        print(f"  config  {change}")


if __name__ == "__main__":
    main()
