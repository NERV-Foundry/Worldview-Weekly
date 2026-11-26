#!/usr/bin/env python3
"""NERV CI Budget V2: offline sealed workflow inventory (review gate, not authorization)."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ".github/workflows/nerv-ci-budget.yml"
MANIFEST = ".github/nerv-ci-budget.json"


# An optional guard must run when its own policy and workflow inputs change,
# not on unrelated application PRs. Never make this path-filtered job required.
EXPECTED_SCOPE = (
    "on:\n"
    "  pull_request:\n"
    "    paths:\n"
    "      - '.github/workflows/**'\n"
    "      - '.github/nerv-ci-budget.json'\n"
    "      - 'tools/nerv_ci_budget_v2.py'\n"
    "      - 'docs/nerv-ci-budget.md'\n"
)

def policy_event_scope_exact(text):
    prefix = "name: NERV CI budget policy\n"
    return (
        text.startswith(prefix + EXPECTED_SCOPE + "permissions:\n")
        and len(re.findall(r"(?m)^on:\s*$", text)) == 1
        and "paths-ignore:" not in text
    )


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest()


# Exact candidate guard seal. Native assignment is deliberately NOT admitted yet.
GUARD_BLOB = "26c86e8de1056177af619c29abab0ba3f777c66e"
ROUTE = ["self-hosted", "Linux", "ARM64", "nerv-ci-trial"]
RUNNER_ADMISSIONS = {
    (GUARD, "policy"): {
        "runner": ROUTE,
        "workflow_blob": GUARD_BLOB,
        "not_before": "2026-11-26T22:44:00Z",
        "expires": "2026-11-29T17:00:00Z",
        "evidence": "https://github.com/NERV-Foundry/stargate/issues/97#issuecomment-7205041141; R24 exact read-only policy eligibility; finite trial only",
    },
}

def admitted_self_hosted(path, job_id, runner, limit, workflow_blob, admissions, now):
    """Exact source admission only; native identity/isolation and queue exit are external gates."""
    entry = admissions.get((str(path), job_id))
    if not isinstance(entry, dict) or set(entry) != {
            "runner", "workflow_blob", "not_before", "expires", "evidence"}:
        return False
    if runner != entry["runner"] or workflow_blob != entry["workflow_blob"]:
        return False
    if not isinstance(workflow_blob, str) or not re.fullmatch(r"[0-9a-f]{40}", workflow_blob):
        return False
    if not isinstance(entry["evidence"], str) or not entry["evidence"].strip():
        return False
    # Both finite and persistent capacity require a bounded reviewed admission.
    # A repository-scoped exact route may be a label array; an organisation
    # route must additionally name its exact non-default runner group.
    labels = runner
    if isinstance(runner, dict):
        if set(runner) != {"group", "labels"}:
            return False
        group = runner["group"]
        if (not isinstance(group, str) or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", group) or
                group.lower() == "default"):
            return False
        labels = runner["labels"]
    if (not isinstance(labels, list) or len(labels) != 4 or
            not all(isinstance(label, str) for label in labels) or
            labels[:2] != ["self-hosted", "Linux"] or
            labels[2] not in ("X64", "ARM64") or
            not re.fullmatch(r"nerv-ci-[a-z0-9][a-z0-9-]{0,48}", labels[3])):
        return False
    if not isinstance(limit, str) or not re.fullmatch(r"(?:[1-9]|1[0-9]|20)", limit):
        return False
    def utc_stamp(value):
        if not isinstance(value, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
            raise ValueError("admission requires canonical UTC timestamps")
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    try:
        start, end = utc_stamp(entry["not_before"]), utc_stamp(entry["expires"])
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
            return False
        return start <= now < end and now + timedelta(minutes=int(limit)) <= end
    except (ValueError, OverflowError):
        return False


def validate(root, *, admissions=None, now=None):
    admissions = RUNNER_ADMISSIONS if admissions is None else admissions
    now = datetime.now(timezone.utc) if now is None else now
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
    if gp.is_symlink() or not gp.is_file():
        errors.append("required PR policy workflow missing")
    else:
        raw = gp.read_bytes()
        text = raw.decode('utf-8')
        digest = git_blob_sha(raw)
        if digest != GUARD_BLOB:
            errors.append('policy workflow differs from independently reviewed seal')
        if not admitted_self_hosted(GUARD, 'policy', ROUTE, '3', digest, admissions, now):
            errors.append('policy runner not admitted for exact job, blob and time window')
        for token in ["name: NERV CI budget policy\n", "on:\n  pull_request:\n",
                      "permissions:\n  contents: read\n", "runs-on: [self-hosted, Linux, ARM64, nerv-ci-trial]",
                      "timeout-minutes: 3", "python3 tools/nerv_ci_budget_v2.py --self-test",
                      "python3 tools/nerv_ci_budget_v2.py",
                      "github.event.pull_request.number || github.ref"]:
            if token not in text:
                errors.append("policy workflow token missing: " + repr(token))
        if "cancel-in-progress: " + chr(36) + "{{ github.event_name == 'pull_request' }}" not in text:
            errors.append("unsafe cancellation")
        if not policy_event_scope_exact(text):
            errors.append("policy event scope differs from approved CI-only paths")
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
        valid = "name: NERV CI budget policy\n" + EXPECTED_SCOPE + "permissions:\n"
        assert policy_event_scope_exact(valid)
        for omitted in (
            ".github/workflows/**", ".github/nerv-ci-budget.json",
            "tools/nerv_ci_budget_v2.py", "docs/nerv-ci-budget.md",
        ):
            assert not policy_event_scope_exact(valid.replace("      - '" + omitted + "'\n", "", 1))
        assert not policy_event_scope_exact(valid.replace("    paths:\n", "    paths-ignore:\n"))
        assert not policy_event_scope_exact(valid.replace("  pull_request:\n", "  push:\n"))
        assert not policy_event_scope_exact(valid.replace("permissions:\n", "  workflow_dispatch:\npermissions:\n"))
        assert not policy_event_scope_exact(valid + "on:\n")
        live = datetime(2026, 11, 26, 23, 0, tzinfo=timezone.utc)
        expiry = datetime(2026, 11, 29, 17, 0, tzinfo=timezone.utc)
        fixture = {(GUARD, "policy"): {"runner": ROUTE, "workflow_blob": GUARD_BLOB,
                    "not_before": "2026-11-26T19:43:00Z", "expires": "2026-11-29T17:00:00Z",
                    "evidence": "synthetic fixture only"}}
        assert admitted_self_hosted(GUARD, "policy", ROUTE, "3", GUARD_BLOB, fixture, live)
        assert admitted_self_hosted(GUARD, "policy", ROUTE, "3", GUARD_BLOB, RUNNER_ADMISSIONS, live)
        assert not admitted_self_hosted(GUARD, "other", ROUTE, "3", GUARD_BLOB, RUNNER_ADMISSIONS, live)
        assert not admitted_self_hosted(GUARD, "policy", ROUTE, "3", GUARD_BLOB, {}, live)
        assert not admitted_self_hosted(GUARD, "policy", ROUTE, "3", GUARD_BLOB, fixture, expiry)
        assert not admitted_self_hosted(GUARD, "policy", ROUTE, "3", GUARD_BLOB, fixture, expiry-timedelta(minutes=1))
        assert not admitted_self_hosted(GUARD, "wrong", ROUTE, "3", GUARD_BLOB, fixture, live)
        assert not admitted_self_hosted("other", "policy", ROUTE, "3", GUARD_BLOB, fixture, live)
        assert not admitted_self_hosted(GUARD, "policy", ROUTE, "3", "0"*40, fixture, live)
        assert not admitted_self_hosted(GUARD, "policy", ["self-hosted", "Linux", "X64", "nerv-ci-trial"], "3", GUARD_BLOB, fixture, live)
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
