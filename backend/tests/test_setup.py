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
