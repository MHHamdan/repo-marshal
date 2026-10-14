#!/usr/bin/env bash
# Run the README quickstart in a clean environment and time it: empty HOME, minimal PATH, no pip
# cache, only network proxy variables kept. Prints each step and the total seconds.
#
#   scripts/quickstart-check.sh [INSTALL_SPEC]
#
# INSTALL_SPEC defaults to the PyPI release ("repomarshal"). Before the first release, pass a git
# spec, e.g. "repomarshal @ git+https://github.com/MHHamdan/repo-marshal@v0.1.0".
set -euo pipefail

SPEC="${1:-repomarshal}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

run_clean() {
  env -i HOME="$WORK/home" PATH="/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ${HTTPS_PROXY:+HTTPS_PROXY="$HTTPS_PROXY"} ${https_proxy:+https_proxy="$https_proxy"} \
    ${NO_PROXY:+NO_PROXY="$NO_PROXY"} ${SSL_CERT_FILE:+SSL_CERT_FILE="$SSL_CERT_FILE"} \
    ${PIP_CERT:+PIP_CERT="$PIP_CERT"} ${GIT_SSL_CAINFO:+GIT_SSL_CAINFO="$GIT_SSL_CAINFO"} \
    bash -euo pipefail -c "$1"
}

mkdir -p "$WORK/home"
start=$(date +%s.%N)

# A tiny project standing in for "your repository": one module, one test, pytest configured.
run_clean "
  mkdir -p '$WORK/proj/src/calc' '$WORK/proj/tests' && cd '$WORK/proj'
  git init -q -b main
  git config user.email you@example.invalid && git config user.name You
  printf '[project]\nname = \"calc\"\nversion = \"0\"\n[tool.pytest.ini_options]\npythonpath = [\"src\"]\n' > pyproject.toml
  printf 'def add(a, b):\n    return a + b\n' > src/calc/__init__.py
  printf 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n' > tests/test_calc.py
  git add -A && git commit -q -m init
"

echo '--- 1. install (venv + pip)'
t=$(date +%s.%N)
run_clean "
  python3 -m venv '$WORK/rm'
  '$WORK/rm/bin/pip' install --quiet '$SPEC' pytest
"
echo "    $(python3 -c "print(round($(date +%s.%N) - $t, 1))") s"

echo '--- 2. change something, then plan'
run_clean "
  cd '$WORK/proj'
  printf 'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n' > src/calc/__init__.py
  printf '\ndef test_sub():\n    from calc import sub\n    assert sub(5, 3) == 2\n' >> tests/test_calc.py
  PATH='$WORK/rm/bin':\$PATH repomarshal plan 'add calc.sub'
"

echo '--- 3. first verdict'
set +e
run_clean "cd '$WORK/proj' && PATH='$WORK/rm/bin':\$PATH repomarshal done"
code=$?
set -e
echo "--- repomarshal done exit code: $code (0 PASS, 1 FAIL, 2 UNKNOWN, 3 BLOCKED)"
echo "--- total: $(python3 -c "print(round($(date +%s.%N) - $start, 1))") s"
exit "$code"
