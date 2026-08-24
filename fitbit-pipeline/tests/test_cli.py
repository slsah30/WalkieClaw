import pytest

from fitbit_pipeline import cli
from fitbit_pipeline.config import Config


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for command in ("auth", "doctor", "backfill", "daily", "serve", "status"):
        assert command in out


def test_commands_are_all_wired_up():
    parser = cli.build_parser()
    subparsers = [
        action for action in parser._actions if action.dest == "command"
    ][0].choices
    assert set(subparsers) == set(cli.COMMANDS)


def test_a_bad_config_exits_two(tmp_path, capsys):
    bad = tmp_path / "config.toml"
    bad.write_text("[nope]\nx = 1\n")
    assert cli.main(["--config", str(bad), "status"]) == 2
    assert "Configuration error" in capsys.readouterr().err


def test_status_on_an_empty_database_says_what_to_run(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(f'[database]\npath = "{tmp_path / "db.sqlite3"}"\n')
    assert cli.main(["--config", str(config), "status"]) == 0
    assert "Nothing synced yet" in capsys.readouterr().out


def test_data_type_allowlist_becomes_a_skip_list():
    config = Config()
    skip = cli._skip_list(config, ["steps"])
    assert "steps" not in skip
    assert "sleep" in skip


def test_an_unknown_data_type_is_refused_with_the_valid_list():
    with pytest.raises(SystemExit, match="steps"):
        cli._skip_list(Config(), ["not-a-type"])


def test_serve_without_a_database_refuses_rather_than_serving_nothing(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(f'[database]\npath = "{tmp_path / "absent.sqlite3"}"\n')
    assert cli.main(["--config", str(config), "serve"]) == 1
    assert "No database" in capsys.readouterr().out


def test_rebuild_recomputes_the_derived_table(tmp_path, capsys):
    from fitbit_pipeline import db

    database = tmp_path / "db.sqlite3"
    conn = db.open_database(database)
    db.upsert(conn, "resting_hr", {"date": "2026-08-17", "bpm": 51}, ("date",))
    assert db.query_one(conn, "SELECT COUNT(*) AS n FROM daily_summary")["n"] == 0
    conn.close()

    config = tmp_path / "config.toml"
    config.write_text(f'[database]\npath = "{database}"\n')
    assert cli.main(["--config", str(config), "rebuild"]) == 0
    assert "Rebuilt daily_summary for 1 day" in capsys.readouterr().out

    conn = db.open_database(database)
    assert db.query_one(conn, "SELECT resting_hr FROM daily_summary WHERE date = '2026-08-17'")[
        "resting_hr"
    ] == 51
    conn.close()


def _serve_config(tmp_path, server_toml: str):
    """A config whose database exists, so serve gets past its first check."""
    import sqlite3

    database = tmp_path / "db.sqlite3"
    sqlite3.connect(database).close()
    config = tmp_path / "config.toml"
    config.write_text(f'[database]\npath = "{database}"\n\n[server]\n{server_toml}')
    return config


def test_serve_binds_the_tailnet_address_when_tailscale_is_set(tmp_path, capsys, monkeypatch):
    from fitbit_pipeline import net

    monkeypatch.setattr(net, "tailscale_ipv4", lambda: "100.101.102.103")
    bound = {}

    def fake_run(app, host, port, **kwargs):
        bound["host"], bound["port"] = host, port

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    config = _serve_config(tmp_path, "tailscale = true\nport = 9000\n")
    assert cli.main(["--config", str(config), "serve"]) == 0
    assert bound == {"host": "100.101.102.103", "port": 9000}
    out = capsys.readouterr().out
    assert "tailnet only" in out
    assert "no authentication" in out


def test_serve_refuses_rather_than_widening_the_bind_when_tailscale_is_absent(
    tmp_path, capsys, monkeypatch
):
    """A missing tailnet address must never silently fall back to 0.0.0.0."""
    from fitbit_pipeline import net

    def boom():
        raise net.TailscaleError("no tailnet address; try tailscale up")

    monkeypatch.setattr(net, "tailscale_ipv4", boom)
    import uvicorn

    def must_not_run(*args, **kwargs):  # pragma: no cover
        raise AssertionError("uvicorn must not start without a resolved bind address")

    monkeypatch.setattr(uvicorn, "run", must_not_run)
    config = _serve_config(tmp_path, "tailscale = true\n")
    assert cli.main(["--config", str(config), "serve"]) == 1
    assert "tailscale up" in capsys.readouterr().out


def test_tailscale_takes_precedence_over_expose_lan(tmp_path, monkeypatch):
    from fitbit_pipeline import net

    monkeypatch.setattr(net, "tailscale_ipv4", lambda: "100.5.5.5")
    bound = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: bound.update(host=host))
    config = _serve_config(tmp_path, "tailscale = true\nexpose_lan = true\n")
    assert cli.main(["--config", str(config), "serve"]) == 0
    assert bound["host"] == "100.5.5.5"


def test_explicit_host_flag_overrides_tailscale(tmp_path, monkeypatch):
    from fitbit_pipeline import net

    def must_not_probe():  # pragma: no cover
        raise AssertionError("--host was given, tailnet detection should not run")

    monkeypatch.setattr(net, "tailscale_ipv4", must_not_probe)
    bound = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: bound.update(host=host))
    config = _serve_config(tmp_path, "tailscale = true\n")
    assert cli.main(["--config", str(config), "serve", "--host", "127.0.0.1"]) == 0
    assert bound["host"] == "127.0.0.1"
