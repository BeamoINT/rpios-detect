import json
from pathlib import Path

import pytest

from rpios_detect.cli import run_rpiv
from rpios_detect.models import EXIT_USAGE
from rpios_detect.session import (
    SESSION_ENV,
    SessionError,
    SessionSnapshot,
    clear_session,
    default_session_path,
    format_session_status,
    load_session,
    save_session,
    snapshot_from_counts,
)


def _snap(**overrides: object) -> SessionSnapshot:
    base = dict(
        started_at="2026-08-24T00:00:00Z",
        checked=4,
        raspberry_pi_os=2,
        not_raspberry_pi_os=1,
        unsure=1,
        last={
            "kind": "raspberry_pi_os",
            "headline": "RASPBERRY PI OS",
            "device": "/dev/disk4",
            "size": "32.0 GB",
            "card_number": 4,
            "os_name": "Raspberry Pi OS",
            "eject_note": "Ejected. Result stays until you insert the next card.",
        },
        tool_version="0.2.0",
    )
    base.update(overrides)
    return snapshot_from_counts(**base)  # type: ignore[arg-type]


def test_roundtrip_session_file(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    original = _snap()
    save_session(path, original)
    loaded = load_session(path)
    assert loaded is not None
    assert loaded.checked == 4
    assert loaded.raspberry_pi_os == 2
    assert loaded.not_raspberry_pi_os == 1
    assert loaded.unsure == 1
    assert loaded.last is not None
    assert loaded.last["kind"] == "raspberry_pi_os"
    assert loaded.last["device"] == "/dev/disk4"
    assert loaded.started_at == "2026-08-24T00:00:00Z"
    assert loaded.updated_at


def test_missing_session_is_none(tmp_path: Path) -> None:
    assert load_session(tmp_path / "nope.json") is None


def test_clear_session(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, _snap())
    assert clear_session(path) is True
    assert path.exists() is False
    assert clear_session(path) is False


def test_corrupt_session_raises(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionError):
        load_session(path)


def test_empty_session_raises(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(SessionError):
        load_session(path)


def test_negative_counts_raise(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"schema": 1, "checked": -1, "raspberry_pi_os": 0, "not_raspberry_pi_os": 0, "unsure": 0}), encoding="utf-8")
    with pytest.raises(SessionError):
        load_session(path)


def test_status_text_includes_counts(tmp_path: Path) -> None:
    snap = _snap()
    text = format_session_status(snap, path=tmp_path / "session.json")
    assert "checked  4" in text
    assert "2 Raspberry Pi OS" in text
    assert "RASPBERRY PI OS" in text
    assert str(tmp_path / "session.json") in text


def test_default_path_honors_env(tmp_path: Path) -> None:
    custom = tmp_path / "custom.json"
    got = default_session_path(environ={SESSION_ENV: str(custom)})
    assert got == custom


def test_default_path_macos() -> None:
    path = default_session_path(environ={}, platform="darwin")
    assert path.as_posix().endswith("Library/Application Support/rpiv/session.json")


def test_default_path_linux_xdg() -> None:
    path = default_session_path(environ={"XDG_STATE_HOME": "/tmp/state"}, platform="linux")
    assert path == Path("/tmp/state/rpiv/session.json")


def test_rpiv_status_json_and_clear(tmp_path: Path, capsys) -> None:
    path = tmp_path / "session.json"
    save_session(path, _snap())
    assert run_rpiv(["--session-file", str(path), "--status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "status"
    assert payload["session"]["checked"] == 4
    assert payload["session"]["raspberry_pi_os"] == 2
    assert run_rpiv(["--session-file", str(path), "--status"]) == 0
    text = capsys.readouterr().out
    assert "checked  4" in text
    assert run_rpiv(["--session-file", str(path), "--clear"]) == 0
    out = capsys.readouterr().out
    assert "Cleared" in out
    assert load_session(path) is None
    assert run_rpiv(["--session-file", str(path), "--clear"]) == 0
    assert "No saved session" in capsys.readouterr().out


def test_rpiv_resume_without_session_is_usage(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert run_rpiv(["--session-file", str(missing), "--resume"]) == EXIT_USAGE


def test_rpiv_resume_and_clear_are_exclusive(tmp_path: Path) -> None:
    assert run_rpiv(["--resume", "--clear"]) == EXIT_USAGE
