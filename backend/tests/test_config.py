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
        "[updates]\ncheck_on_start = false\ncheck_every_hour = false\n"
    )
    settings = load_settings("practice")
    assert settings.playground.preview_rows == 50
    assert settings.repos.knowledge == "SripadaLab-UM/ihs-knowledge"
    assert settings.repos.pipelines is None
    assert settings.workflows == WorkflowSettings(max_concurrent_runs=2)
    assert settings.updates.check_on_start is False
    assert settings.updates.check_every_hour is False
    assert UpdateSettings().check_every_hour is True
    assert settings.updates.repository == UpdateSettings.repository
    assert settings.updates.channel == "auto"


def test_releases_come_from_the_app_repo_by_its_real_name(settings_file):
    # The repo's name has a hyphen; "ihs_datalab" is only the local folder's.
    assert UpdateSettings().repository == "SripadaLab-UM/ihs-datalab"
    settings_file.write_text('[updates]\nchannel = "stable"\n')
    assert load_settings("practice").updates.channel == "stable"


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
        ('[updates]\ncheck_every_hour = "yes"\n', "updates.check_every_hour"),
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
        ('[updates]\nchannel = "nightly"\n', "updates.channel"),
        ('[repos]\nclient_id = "Iv23 x; rm"\n', "client id"),
        ("[playground]\npreview_rows = 1.5\n", "playground.preview_rows"),
    ],
)
def test_mistakes_in_a_section_are_refused_with_a_clear_message(settings_file, toml, message):
    settings_file.write_text(toml)
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_settings("practice")


def test_the_real_database_cant_use_the_practice_keychain_entry(settings_file):
    """Practice DataLab saves the synthetic database's public password there."""
    settings_file.write_text(
        '[oracle]\nhost = "db.example.org"\nservice = "SVC"\nuser = "DATALAB_RO"\n'
        'allowed_schemas = ["IHS_2025"]\nkeychain_service = "datalab-practice"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="practice DataLab's own keychain entry"):
        load_settings("real")


def test_optional_text_takes_text_and_numbers_take_whole_numbers():
    from datalab.config import _fits

    assert _fits(str | None, "lab/kb") and not _fits(str | None, 3)
    assert _fits(int, 3) and not _fits(int, True) and not _fits(int, 1.5)
    assert _fits(float, 3) and _fits(float, 1.5) and not _fits(float, False)
    with pytest.raises(TypeError):
        _fits(list[str], ["a"])  # a section field type not handled yet: said so, not guessed


PINNED_AGENT = "ghcr.io/sripadalab-um/datalab-agent@sha256:" + "a" * 64


def test_an_installed_real_datalab_only_runs_a_pinned_agent_image(settings_file, monkeypatch):
    from datalab import config

    monkeypatch.setenv("DATALAB_PROFILE", "real")
    monkeypatch.setattr(config, "_release_agent_image", lambda: PINNED_AGENT)
    assert load_settings("real").agent_image == PINNED_AGENT
    monkeypatch.setenv("DATALAB_AGENT_IMAGE", "ghcr.io/sripadalab-um/datalab-agent:latest")
    with pytest.raises(ValueError, match="isn't pinned by digest"):
        load_settings("real")
    monkeypatch.setenv("DATALAB_AGENT_IMAGE", PINNED_AGENT.replace("a" * 64, "b" * 64))
    assert load_settings("real").agent_image.endswith("b" * 64)
    # Practice may run a local image.
    monkeypatch.setenv("DATALAB_AGENT_IMAGE", "datalab-agent:dev")
    assert load_settings("practice").agent_image == "datalab-agent:dev"


def test_a_development_copy_warns_about_an_unpinned_image(settings_file, monkeypatch, caplog):
    from datalab import config

    monkeypatch.setattr(config, "_release_agent_image", lambda: None)
    monkeypatch.setenv("DATALAB_AGENT_IMAGE", "datalab-agent:dev")
    assert load_settings("real").agent_image == "datalab-agent:dev"
    assert "NOT PINNED BY DIGEST" in caplog.text


RELEASE_KEY = {"lab": {"dropbox": {"app_key": "relkey123456"}}}


@pytest.mark.parametrize(
    ("release", "toml", "expected"),
    [
        # An install from before the processed data: the release brings the key.
        (RELEASE_KEY, "", "relkey123456"),
        # The lab's own settings win.
        (RELEASE_KEY, '[dropbox]\napp_key = "ownkey123456"\n', "ownkey123456"),
        # A development build carries none; a broken one is ignored, never fatal.
        ({}, "", None),
        ({"lab": {"dropbox": {"app_key": "Not A Key!"}}}, "", None),
        ({"lab": "nonsense"}, "", None),
    ],
)
def test_the_release_carries_the_labs_dropbox_app_key(
    settings_file, monkeypatch, release, toml, expected
):
    from datalab import config

    monkeypatch.setattr(config, "_release_info", lambda: release)
    settings_file.write_text(toml, encoding="utf-8")
    assert load_settings("real").dropbox.app_key == expected
    if not toml:
        # Practice gets it too: it never uses Dropbox, but the key is its switch
        # for the synthetic folder, so practice offers what the real one does.
        assert load_settings("practice").dropbox.app_key == expected


SERVER = """deployment = "server"
[server]
public_url = "https://datalab.example.edu"
admins = [1234]
"""


def test_a_server_needs_its_own_section(settings_file):
    assert load_settings("practice").deployment == "desktop"
    assert load_settings("practice").server is None
    settings_file.write_text(SERVER, encoding="utf-8")
    settings = load_settings("practice")
    assert settings.on_server and settings.server is not None
    assert settings.server.site == "https://datalab.example.edu/datalab"
    assert settings.server.admins == (1234,)


@pytest.mark.parametrize(
    "text",
    [
        '[server]\npublic_url = "https://a.edu"\n',  # a desktop with [server]
        'deployment = "cloud"\n',
        'deployment = "server"\n',  # no public_url
        SERVER.replace("https://datalab.example.edu", "http://datalab.example.edu"),
        SERVER.replace("https://datalab.example.edu", "https://datalab.example.edu/x"),
        SERVER.replace("[1234]", '["octocat"]'),  # a login, not an id
        SERVER + 'base_path = "/datalab/"\n',
        SERVER + "max_running_per_user = 4\n",  # more than max_running
        SERVER + "unknown = 1\n",
    ],
)
def test_server_settings_are_checked(settings_file, text):
    settings_file.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings("practice")
