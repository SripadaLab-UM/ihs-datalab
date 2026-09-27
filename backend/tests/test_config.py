"""Loading settings.toml, in particular the per-area sections."""

from __future__ import annotations

import pytest

from datalab.config import (
    PlaygroundSettings,
    RepoSettings,
    UpdateSettings,
    WorkflowSettings,
    load_settings,
)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    return tmp_path / "settings.toml"


def test_the_sections_have_safe_defaults_without_a_settings_file(settings_file):
    settings = load_settings("practice")
    assert settings.playground == PlaygroundSettings()
    # No lab repos until they're configured: nothing is cloned or synced.
    assert settings.repos == RepoSettings(knowledge=None, pipelines=None)
    assert settings.workflows.max_concurrent_runs == 1
    assert settings.updates == UpdateSettings()


def test_sections_are_read_from_their_tables(settings_file):
    settings_file.write_text(
        "port = 8799\n"
        "[playground]\npreview_rows = 50\n"
        '[repos]\nknowledge = "SripadaLab-UM/ihs-knowledge"\n'
        "[workflows]\nmax_concurrent_runs = 2\n"
        "[updates]\ncheck_on_start = false\n"
    )
    settings = load_settings("practice")
    assert settings.playground.preview_rows == 50
    assert settings.repos.knowledge == "SripadaLab-UM/ihs-knowledge"
    assert settings.repos.pipelines is None
    assert settings.workflows == WorkflowSettings(max_concurrent_runs=2)
    assert settings.updates.check_on_start is False
    assert settings.updates.repository == UpdateSettings.repository


def test_sections_are_frozen(settings_file):
    settings = load_settings("practice")
    with pytest.raises(AttributeError):
        settings.playground.preview_rows = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("[playground]\npreview_rowz = 5\n", "Unknown settings in [playground]"),
        ('[playground]\npreview_rows = "5"\n', "playground.preview_rows"),
        ("[updates]\ncheck_on_start = 1\n", "updates.check_on_start"),
        ("[workflows]\nmax_concurrent_runs = true\n", "workflows.max_concurrent_runs"),
        ("[repos]\nknowledge = 3\n", "repos.knowledge"),
        ('repos = "x"\n', "[repos]"),
        ("[playground]\npreview_rows = 0\n", "between 1 and 500"),
        ("[workflows]\nmax_concurrent_runs = 0\n", "at least 1"),
        ('[repos]\nknowledge = "--upload-pack=touch /tmp/x"\n', "owner/name"),
        ('[repos]\npipelines = "-lab/ihs-pipelines"\n', "owner/name"),
        ('[repos]\nknowledge = "lab/-x"\n', "owner/name"),
        ('[repos]\nknowledge = "lab/.."\n', "owner/name"),
        ('[repos]\nknowledge = "https://github.com/lab/kb"\n', "owner/name"),
        ('[updates]\nrepository = "lab"\n', "owner/name"),
        ('[repos]\nclient_id = "Iv23 x; rm"\n', "client id"),
        ("[playground]\npreview_rows = 1.5\n", "playground.preview_rows"),
    ],
)
def test_mistakes_in_a_section_are_refused_with_a_clear_message(settings_file, toml, message):
    settings_file.write_text(toml)
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_settings("practice")


def test_optional_text_takes_text_and_numbers_take_whole_numbers():
    from datalab.config import _fits

    assert _fits(str | None, "lab/kb") and not _fits(str | None, 3)
    assert _fits(int, 3) and not _fits(int, True) and not _fits(int, 1.5)
    assert _fits(float, 3) and _fits(float, 1.5) and not _fits(float, False)
    with pytest.raises(TypeError):
        _fits(list[str], ["a"])  # a section field type not handled yet: said so, not guessed
