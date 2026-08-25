#!/usr/bin/env bash
# Install `rpiv` (and `rpios-detect`) for the current user.
# Works from a clone (`./install.sh`) or:
#   curl -fsSL https://raw.githubusercontent.com/BeamoINT/rpios-detect/main/install.sh | bash
#
# Do not use sudo. This is a user-level install under ~/.local/bin.
set -euo pipefail

REPO_GIT="${RPIOS_DETECT_GIT:-git+https://github.com/BeamoINT/rpios-detect.git}"

HERE=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" && "${BASH_SOURCE[0]}" != "-bash" && "${BASH_SOURCE[0]}" != "/dev/stdin" ]]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# If a PATH entry exists but is not runnable (common: Homebrew python3.11
# Mach-O lost +x while the symlink stayed 755), restore execute bits when
# we own the file. zsh then reports "permission denied: rpiv" because the
# shebang interpreter cannot be exec'd.
restore_python_exec() {
  local resolved="$1"
  local target=""
  [[ -e "${resolved}" ]] || return 1
  if [[ -x "${resolved}" ]] && "${resolved}" -c 'import sys' >/dev/null 2>&1; then
    return 0
  fi
  if command -v realpath >/dev/null 2>&1; then
    target="$(realpath "${resolved}" 2>/dev/null || true)"
  fi
  if [[ -z "${target}" ]]; then
    target="$(readlink "${resolved}" 2>/dev/null || true)"
    if [[ -n "${target}" && "${target}" != /* ]]; then
      target="$(cd "$(dirname "${resolved}")" && pwd)/${target}"
    fi
  fi
  [[ -n "${target}" && -f "${target}" ]] || return 1
  [[ -O "${target}" ]] || return 1
  chmod u+x,go+rx "${target}" 2>/dev/null || return 1
  [[ -x "${resolved}" ]] && "${resolved}" -c 'import sys' >/dev/null 2>&1
}

python_ok() {
  local bin="$1"
  [[ -n "${bin}" && -e "${bin}" ]] || return 1
  restore_python_exec "${bin}" || true
  [[ -x "${bin}" ]] || return 1
  "${bin}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

all_python_candidates() {
  local c resolved real
  for c in \
    python3.11 \
    python3.12 \
    python3.13 \
    python3 \
    /opt/homebrew/opt/python@3.11/bin/python3.11 \
    /opt/homebrew/opt/python@3.12/bin/python3.12 \
    /opt/homebrew/opt/python@3.13/bin/python3.13 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3.13 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
  do
    if [[ "${c}" = /* ]]; then
      resolved="${c}"
    else
      resolved="$(command -v "${c}" 2>/dev/null)" || continue
    fi
    python_ok "${resolved}" || continue
    real="$(realpath "${resolved}" 2>/dev/null || printf '%s\n' "${resolved}")"
    printf '%s\n' "${real}"
  done | awk 'NF && !seen[$0]++'
}

python_has_pkg() {
  "$1" -c 'from rpios_detect.cli import rpiv_main' >/dev/null 2>&1
}

pick_python() {
  local resolved first=""
  while IFS= read -r resolved; do
    [[ -n "${first}" ]] || first="${resolved}"
    if python_has_pkg "${resolved}"; then
      printf '%s\n' "${resolved}"
      return 0
    fi
  done < <(all_python_candidates)
  [[ -n "${first}" ]] || return 1
  printf '%s\n' "${first}"
}

ensure_user_venv() {
  local base_py="$1"
  local venv="${HOME}/.local/share/rpios-detect/venv"
  mkdir -p "$(dirname "${venv}")"
  if [[ ! -x "${venv}/bin/python" ]]; then
    "${base_py}" -m venv "${venv}"
  fi
  restore_python_exec "${venv}/bin/python" || true
  if ! python_ok "${venv}/bin/python"; then
    "${base_py}" -m venv --clear "${venv}"
  fi
  python_ok "${venv}/bin/python" || return 1
  printf '%s\n' "${venv}/bin/python"
}

pip_install_pkg() {
  local py="$1"
  if local_checkout; then
    if "${py}" -m pip install -e "${HERE}"; then
      return 0
    fi
    echo "pip blocked on ${py}; installing into a user venv."
    py="$(ensure_user_venv "${py}")"
    PY="${py}"
    "${py}" -m pip install -U pip
    "${py}" -m pip install -e "${HERE}"
    return 0
  fi
  if command -v pipx >/dev/null 2>&1; then
    echo "Installing with pipx from ${REPO_GIT}"
    pipx install "${REPO_GIT}" --force
    return 0
  fi
  if "${py}" -m pip install --user "${REPO_GIT}"; then
    return 0
  fi
  echo "pip blocked on ${py}; installing into a user venv."
  py="$(ensure_user_venv "${py}")"
  PY="${py}"
  "${py}" -m pip install -U pip
  "${py}" -m pip install "${REPO_GIT}"
}

local_checkout() {
  [[ -n "${HERE}" && -f "${HERE}/pyproject.toml" ]] || return 1
  grep -q 'name = "rpios-detect"' "${HERE}/pyproject.toml"
}

write_launcher() {
  local dest="$1"
  local func="$2"
  local argv0="$3"
  local hint
  if [[ -n "${HERE}" ]]; then
    hint="${HERE}/install.sh"
  else
    hint="./install.sh from a rpios-detect clone"
  fi
  mkdir -p "$(dirname "${dest}")"
  cat >"${dest}" <<EOF
#!/bin/sh
# ${argv0} — user-level launcher. Do not use sudo.
PY="${PY}"
run() {
  py="\$1"
  shift
  [ -n "\$py" ] && [ -x "\$py" ] || return 1
  "\$py" -c 'from rpios_detect.cli import ${func}' >/dev/null 2>&1 || return 1
  exec "\$py" -c 'import sys; from rpios_detect.cli import ${func}; sys.argv[0] = "${argv0}"; raise SystemExit(${func}())' "\$@"
}
run "\$PY" "\$@"
for py in \\
  /opt/homebrew/opt/python@3.13/bin/python3.13 \\
  /opt/homebrew/opt/python@3.12/bin/python3.12 \\
  /opt/homebrew/opt/python@3.11/bin/python3.11 \\
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \\
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \\
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
do
  run "\$py" "\$@"
done
for c in python3.13 python3.12 python3.11 python3; do
  bin="\$(command -v "\$c" 2>/dev/null)" || continue
  run "\$bin" "\$@"
done
echo "error: ${argv0} cannot find a working Python 3.11+ with rpios-detect installed." >&2
if [ ! -x "\$PY" ]; then
  echo "       interpreter is not executable: \$PY" >&2
  echo "       if you own that file: chmod +x \\"\$PY\\"" >&2
fi
echo "hint: ${hint}   (never sudo)" >&2
exit 126
EOF
  chmod 755 "${dest}"
}

ensure_cmd() {
  local name="$1"
  local func="$2"
  local dest="${HOME}/.local/bin/${name}"
  mkdir -p "${HOME}/.local/bin"
  write_launcher "${dest}" "${func}" "${name}"
}

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "error: do not run this installer as root / with sudo." >&2
  echo "       rpiv is a user-level command under ~/.local/bin." >&2
  exit 1
fi

if ! PY="$(pick_python)"; then
  echo "error: need Python 3.11 or newer on PATH (python3.11, python3.12, …)" >&2
  echo "       if python3.11 exists but zsh says permission denied, restore +x:" >&2
  echo '         chmod +x "$(realpath "$(command -v python3.11)")"' >&2
  exit 1
fi

echo "Using ${PY} ($("${PY}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"

if local_checkout; then
  echo "Installing from ${HERE}"
else
  echo "Installing from ${REPO_GIT}"
fi
pip_install_pkg "${PY}"
echo "Launcher Python: ${PY}"

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

if ! rpiv --version >/dev/null 2>&1; then
  echo "error: rpiv is installed but would not run. Do not use sudo." >&2
  rpiv --version || true
  exit 1
fi

echo
echo "Installed:"
command -v rpiv
rpiv --version
echo
echo "Start the card station:"
echo "  rpiv"
echo "Continue a saved pile:"
echo "  rpiv --resume"
echo
echo "If a new terminal cannot find rpiv, add ~/.local/bin to PATH."
echo "Never run sudo rpiv."
