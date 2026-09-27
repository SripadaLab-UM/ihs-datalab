"""The release workflow's shape: the signing key is in one small job only."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


def jobs() -> dict:
    parsed = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(parsed, dict) and isinstance(parsed.get("jobs"), dict)
    return parsed["jobs"]


def environment(job: dict) -> str | None:
    value = job.get("environment")
    return value.get("name") if isinstance(value, dict) else value


def test_only_the_sign_job_is_in_the_release_environment():
    found = {name for name, job in jobs().items() if environment(job) == "release"}
    assert found == {"sign"}


def test_only_the_sign_job_reads_the_signing_key():
    for name, job in jobs().items():
        text = yaml.safe_dump(job)
        assert ("RELEASE_SIGNING_KEY" in text and "secrets." in text) == (name == "sign"), name


def test_the_sign_job_runs_no_npm_and_no_build():
    sign = jobs()["sign"]
    assert sign["needs"] == "package"
    text = yaml.safe_dump(sign["steps"])
    for forbidden in ("npm", "setup-node", "build-release", "docker", "vite"):
        assert forbidden not in text, forbidden
    assert "uv sync --locked" in text and "sign-release.py" in text
    uploads = [s for s in sign["steps"] if str(s.get("uses", "")).startswith("actions/upload")]
    assert [u["with"]["path"] for u in uploads] == ["signature/SHA256SUMS.sig"]
    assert sign["permissions"] == {"contents": "read"}


def test_publishing_waits_for_the_signature_and_nothing_else_publishes():
    all_jobs = jobs()
    assert set(all_jobs["publish"]["needs"]) == {"package", "sign"}
    creating = [n for n, j in all_jobs.items() if "gh release create" in yaml.safe_dump(j)]
    assert creating == ["publish"]
    assert all_jobs["package"]["permissions"] == {"contents": "read"}
