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
