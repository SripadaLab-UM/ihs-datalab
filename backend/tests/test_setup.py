import datalab.setup as installing
from datalab.config import load_settings


def test_setup_saves_settings_where_datalab_reads_them(tmp_path, monkeypatch):
    # The profile and folder come from the environment, as for the app itself.
    monkeypatch.setenv("DATALAB_PROFILE", "practice")
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setattr(installing, "model_api_key", lambda: "saved")  # no prompt
    lab = tmp_path / "lab.toml"
    lab.write_text("port = 8799\n")

    assert installing.setup(None, lab, update=False) == 0

    assert (tmp_path / "custom" / "settings.toml").exists()
    settings = load_settings()
    assert settings.profile == "practice" and settings.port == 8799


def test_the_summary_never_calls_an_unverified_check_a_pass():
    from datalab.safety import CheckResult, SafetyReport
    from datalab.trial import summary

    ok = CheckResult("a", "p", "A", "pass")
    optional = CheckResult("b", "p", "B", "skip", required=False)
    unverified = CheckResult("c", "p", "The database is read-only", "skip")
    assert summary(SafetyReport("", results=[ok, optional]), strict=False) == "All checks passed."
    for strict in (False, True):
        line = summary(SafetyReport("", results=[ok, unverified]), strict=strict)
        assert "All checks passed" not in line and "The database is read-only" in line
    failed = SafetyReport("", results=[CheckResult("d", "p", "D", "fail")])
    assert summary(failed, strict=False) == "SOME CHECKS FAILED."


def test_a_failed_image_download_gets_the_hint_for_its_cause():
    from datalab.cli import _pull_failure_hint as hint

    not_allowed = [
        # Windows, not in docker-users (or not signed in again since being added)
        'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/'
        'images/create": open //./pipe/dockerDesktopLinuxEngine: Access is denied.',
        # Mac and Linux
        "permission denied while trying to connect to the Docker daemon socket at "
        'unix:///var/run/docker.sock: Post "http://%2Fvar%2Frun%2Fdocker.sock/v1.47/images/create"',
    ]
    not_running = [
        "error during connect: this error may indicate that the docker daemon is not running: "
        "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?",
    ]
    refused = [
        'Error response from daemon: Head "https://ghcr.io/v2/org/agent/manifests/sha256:ab": '
        "unauthorized",
        "Error response from daemon: pull access denied for ghcr.io/org/agent, repository does "
        "not exist or may require 'docker login': "
        "denied: requested access to the resource is denied",
    ]
    for error in not_allowed:
        assert "docker-users" in hint(error), error
    for error in not_running:
        assert "doesn't seem to be running" in hint(error), error
    for error in refused:
        assert "registry" in hint(error), error
    offline = (
        'Error response from daemon: Get "https://ghcr.io/v2/": dial tcp: lookup ghcr.io: '
        "no such host"
    )
    assert "internet connection" in hint(offline)


def test_uninstalling_carries_on_when_docker_does_not_answer(monkeypatch, capsys):
    import subprocess

    calls = []

    def stuck(command, **options):
        calls.append((command, options.get("timeout")))
        raise subprocess.TimeoutExpired(command, options.get("timeout") or 0)

    monkeypatch.setattr(installing.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(installing.subprocess, "run", stuck)

    installing._docker_quiet("ps", "-aq", then="rm -f")  # returns instead of hanging

    assert calls == [(["docker", "ps", "-aq"], 30)]
    assert "Docker didn't answer" in capsys.readouterr().out


def test_uninstalling_removes_what_docker_lists_with_a_time_limit(monkeypatch):
    import subprocess

    calls = []

    def docker(command, **options):
        calls.append((command, options.get("timeout")))
        return subprocess.CompletedProcess(command, 0, stdout="a1\nb2\n" if len(calls) == 1 else "")

    monkeypatch.setattr(installing.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(installing.subprocess, "run", docker)

    installing._docker_quiet("ps", "-aq", then="rm -f")

    assert calls == [(["docker", "ps", "-aq"], 30), (["docker", "rm", "-f", "a1", "b2"], 120)]
