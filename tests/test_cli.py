import json
from pathlib import Path

from rpios_detect.cli import run, run_rpiv
from rpios_detect.models import (
    EXIT_AMBIGUOUS,
    EXIT_NO_MEDIA,
    EXIT_NOT_RPIOS,
    EXIT_RPIOS,
    EXIT_USAGE,
    RESULT_REQUIRED_KEYS,
)
from rpios_detect.scan import exit_code_for

from helpers import ISSUE_STAGE2, ISSUE_STAGE5, META_RPIOS, files


def _write_boot(tmp: Path, filemap: dict) -> Path:
    boot = tmp / "boot"
    boot.mkdir()
    for name, content in filemap.items():
        path = boot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    return boot


def test_json_schema_and_exit_0(tmp_path: Path, capsys) -> None:
    boot = _write_boot(tmp_path, files({"issue.txt": ISSUE_STAGE5, "meta-data": META_RPIOS}))
    code = run(["--json", str(boot)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == EXIT_RPIOS
    assert set(payload) >= {"tool", "tool_version", "scanned_at", "host", "results"}
    assert payload["tool"] == "rpios-detect"
    result = payload["results"][0]
    for key in RESULT_REQUIRED_KEYS:
        assert key in result
    assert result["verdict"] == "raspberry_pi_os"
    assert result["confidence"] == "certain"
    assert result["edition"] == "full"
    assert result["image_date"] == "2026-06-18"
    assert result["pi_gen_stage"] == 5


def test_firmware_only_exit_not_rpios(tmp_path: Path, capsys) -> None:
    boot = _write_boot(tmp_path, files())
    code = run(["--json", str(boot)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["verdict"] != "raspberry_pi_os"
    assert code in {EXIT_NOT_RPIOS, EXIT_AMBIGUOUS}


def test_lite_edition_via_cli(tmp_path: Path, capsys) -> None:
    boot = _write_boot(tmp_path, files({"issue.txt": ISSUE_STAGE2}))
    code = run(["--json", str(boot)])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_RPIOS
    assert payload["results"][0]["edition"] == "lite"


def test_missing_target_is_usage() -> None:
    assert run(["/no/such/rpios-detect-target"]) == EXIT_USAGE


def test_exit_code_for_empty() -> None:
    assert exit_code_for([]) == EXIT_NO_MEDIA


def test_rpiv_help(capsys) -> None:
    assert run_rpiv(["--help"]) == 0
    text = capsys.readouterr().out
    assert "rpiv --resume" in text
    assert "Never sudo" in text


def test_rpiv_version() -> None:
    assert run_rpiv(["--version"]) == 0


def test_rpiv_path_is_oneshot(tmp_path: Path, capsys) -> None:
    boot = _write_boot(tmp_path, files({"issue.txt": ISSUE_STAGE5, "meta-data": META_RPIOS}))
    code = run_rpiv(["--json", str(boot)])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_RPIOS
    assert payload["results"][0]["verdict"] == "raspberry_pi_os"
