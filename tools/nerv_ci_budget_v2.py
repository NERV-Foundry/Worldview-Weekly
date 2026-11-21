#!/usr/bin/env python3
"""NERV CI Budget V2: offline sealed workflow inventory (review gate, not authorization)."""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ".github/workflows/nerv-ci-budget.yml"
MANIFEST = ".github/nerv-ci-budget.json"


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest()


def validate(root):
    errors = []
    try:
        m = json.loads((root / MANIFEST).read_text())
    except (OSError, ValueError):
        return ["manifest absent or invalid"]
    if not isinstance(m, dict) or set(m) != {"schema", "owner", "max_workflows", "workflows"}:
        return ["unexpected manifest keys"]
    if m["schema"] != "NERV_CI_BUDGET_V2" or m["owner"] != "NERV":
        errors.append("policy version or owner mismatch")
    cap, entries = m["max_workflows"], m["workflows"]
    if type(cap) is not int or cap < 0 or cap > 100 or not isinstance(entries, dict):
        return ["invalid workflow ceiling or inventory"]
    if len(entries) > cap:
        errors.append("workflow ceiling exceeded")
    observed = sorted(p.relative_to(root).as_posix() for p in
                      (root / ".github/workflows").glob("*.y*ml"))
    expected = sorted([GUARD] + list(entries))
    if observed != expected:
        errors.append("workflow inventory drift: expected " + repr(expected) + ", got " + repr(observed))
    gp = root / GUARD
    if not gp.is_file():
        errors.append("required PR policy workflow missing")
    else:
        text = gp.read_text()
        for token in ["name: NERV CI budget policy\n", "on:\n  pull_request:\n",
                      "permissions:\n  contents: read\n", "runs-on: ubuntu-slim",
                      "timeout-minutes: 3", "python3 tools/nerv_ci_budget_v2.py --self-test",
                      "python3 tools/nerv_ci_budget_v2.py",
                      "github.event.pull_request.number || github.ref"]:
            if token not in text:
                errors.append("policy workflow token missing: " + repr(token))
        if "cancel-in-progress: " + chr(36) + "{{ github.event_name == 'pull_request' }}" not in text:
            errors.append("unsafe cancellation")
        if re.search(r"(?m)^    (?:paths|paths-ignore|if):", text) or re.search(
                r"(?m)^  (?:push|schedule|workflow_dispatch):", text):
            errors.append("policy must run on every PR only")
        if len(re.findall(r"(?m)^  [a-zA-Z][\w-]*:\s*$", text.split("\njobs:", 1)[-1])) != 1:
            errors.append("policy must use exactly one runner job")
    for path, item in entries.items():
        if not isinstance(path, str) or not re.fullmatch(
                r"\.github/workflows/[a-zA-Z0-9_.-]+\.ya?ml", path) or path == GUARD:
            errors.append("bad workflow path: " + repr(path))
            continue
        if not isinstance(item, dict) or set(item) != {"git_blob_sha", "role", "reason"}:
            errors.append(path + ": invalid workflow metadata")
            continue
        digest, role, reason = item["git_blob_sha"], item["role"], item["reason"]
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{40}", digest):
            errors.append(path + ": bad content fingerprint")
        if role not in ("ci", "transactional", "dormant", "legacy"):
            errors.append(path + ": unknown workflow role")
        if not isinstance(reason, str) or len(reason.strip()) < 18:
            errors.append(path + ": explicit exception reason required")
        f = root / path
        if not f.is_file():
            errors.append(path + ": missing workflow")
        elif git_blob_sha(f.read_bytes()) != digest:
            errors.append(path + ": workflow changed; update sealed inventory through review")
    return errors


def main():
    if "--self-test" in sys.argv:
        assert git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
        print("NERV_CI_BUDGET_SELF_TEST=PASS")
        return 0
    errors = validate(ROOT)
    for error in errors:
        print("NERV_CI_BUDGET_FAIL", error)
    if errors:
        return 1
    print("NERV_CI_BUDGET_POLICY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
