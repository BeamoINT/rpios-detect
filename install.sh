#!/usr/bin/env bash
# Install `rpiv` (and `rpios-detect`) for the current user.
# Works from a clone (`./install.sh`) or:
#   curl -fsSL https://raw.githubusercontent.com/BeamoINT/rpios-detect/main/install.sh | bash
set -euo pipefail

REPO_GIT="${RPIOS_DETECT_GIT:-git+https://github.com/BeamoINT/rpios-detect.git}"

HERE=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" && "${BASH_SOURCE[0]}" != "-bash" && "${BASH_SOURCE[0]}" != "/dev/stdin" ]]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        printf '%s\n' "$c"
        return 0
      fi
    fi
  done
  return 1
}

local_checkout() {
  [[ -n "${HERE}" && -f "${HERE}/pyproject.toml" ]] || return 1
  grep -q 'name = "rpios-detect"' "${HERE}/pyproject.toml"
}

write_launcher() {
  local dest="$1"
  local func="$2"
  local argv0="$3"
  local pybin
  pybin="$(command -v "${PY}")"
  mkdir -p "$(dirname "${dest}")"
  cat >"${dest}" <<EOF
#!${pybin}
import sys
from rpios_detect.cli import ${func}

if __name__ == "__main__":
    sys.argv[0] = "${argv0}"
    raise SystemExit(${func}())
EOF
  chmod +x "${dest}"
}

ensure_cmd() {
  local name="$1"
  local func="$2"
  local dest="${HOME}/.local/bin/${name}"
  mkdir -p "${HOME}/.local/bin"
  if [[ -x "${dest}" ]]; then
    return 0
  fi
  write_launcher "${dest}" "${func}" "${name}"
}

if ! PY="$(pick_python)"; then
  echo "error: need Python 3.11 or newer on PATH (python3.11, python3.12, …)" >&2
  exit 1
fi

echo "Using ${PY} ($("${PY}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"

if local_checkout; then
  echo "Installing from ${HERE}"
  "${PY}" -m pip install -e "${HERE}"
else
  if command -v pipx >/dev/null 2>&1; then
    echo "Installing with pipx from ${REPO_GIT}"
    pipx install "${REPO_GIT}" --force
  else
    echo "Installing with pip from ${REPO_GIT}"
    "${PY}" -m pip install --user "${REPO_GIT}"
  fi
fi

hash -r 2>/dev/null || true
export PATH="${HOME}/.local/bin:${PATH}"

ensure_cmd rpiv rpiv_main
ensure_cmd rpios-detect main

hash -r 2>/dev/null || true

if ! command -v rpiv >/dev/null 2>&1; then
  echo "Installed, but rpiv is not on PATH yet." >&2
  echo "Add this to your shell profile, then open a new terminal:" >&2
  echo '  export PATH="$HOME/.local/bin:$PATH"' >&2
  exit 1
fi

echo
echo "Installed:"
command -v rpiv
rpiv --version
echo
echo "Start the card station:"
echo "  rpiv"
echo
echo "If a new terminal cannot find rpiv, add ~/.local/bin to PATH."
