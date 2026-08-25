import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_install_sh_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)
