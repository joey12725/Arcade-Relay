import pathlib
import runpy


def client(monkeypatch, tmp_path, contents=None, override=None, media_env=None):
    config = tmp_path / "cabinet.env"
    if contents is not None:
        config.write_text(contents)
    for key, value in (("ARCADED_URL", override), ("CABD_MEDIA_BASE", media_env)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    source = pathlib.Path(__file__).parents[1] / "server/arcadectl"
    return runpy.run_path(str(source))["server_url"](config)


def test_cabinet_config_works_without_shell_environment(monkeypatch, tmp_path):
    result = client(monkeypatch, tmp_path, '# comment\nSERVER_WS_URL=wss://example/cab\nCABD_MEDIA_BASE="https://arcade.example.com/"\n')
    assert result == "https://arcade.example.com"


def test_explicit_override_wins_over_cabinet_configuration(monkeypatch, tmp_path):
    result = client(monkeypatch, tmp_path, "CABD_MEDIA_BASE=https://cabinet-config\n", "https://override/", "https://media-env")
    assert result == "https://override"


def test_server_localhost_default_is_preserved(monkeypatch, tmp_path):
    assert client(monkeypatch, tmp_path) == "http://localhost:8000"
