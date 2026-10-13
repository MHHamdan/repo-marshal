#!/usr/bin/env bash
# Enforce a single contributor: every commit must be authored and committed by
# the repo owner, with no co-author trailers or AI-assistant attribution.
#   scripts/check-commit-identity.sh <rev-range>     audit existing commits
#   scripts/check-commit-identity.sh --msg <file>    commit-msg hook mode
set -euo pipefail

OWNER_EMAIL="mh2022ets@gmail.com"
BAD_MSG='^Co-Authored-By:|noreply@anthropic\.com|Generated with \[Claude|🤖 Generated with'

fail=0
check_msg() { # $1 = label, $2 = message text
  local hits
  if hits=$(printf '%s\n' "$2" | grep -inE "$BAD_MSG"); then
    echo "✗ $1: forbidden attribution in message:"; printf '%s\n' "$hits" | sed 's/^/    /'; fail=1
  fi
}

if [[ "${1:-}" == "--msg" ]]; then
  author=$(git var GIT_AUTHOR_IDENT | sed -E 's/.*<(.*)>.*/\1/')
  committer=$(git var GIT_COMMITTER_IDENT | sed -E 's/.*<(.*)>.*/\1/')
  [[ "$author" == "$OWNER_EMAIL" ]] || { echo "✗ author email is $author, expected $OWNER_EMAIL"; fail=1; }
  [[ "$committer" == "$OWNER_EMAIL" ]] || { echo "✗ committer email is $committer, expected $OWNER_EMAIL"; fail=1; }
  check_msg "commit" "$(grep -v '^#' "$2")"
  exit $fail
fi

range="${1:?usage: $0 <rev-range> | --msg <file>}"
for sha in $(git rev-list "$range"); do
  ae=$(git log -1 --format=%ae "$sha"); ce=$(git log -1 --format=%ce "$sha")
  [[ "$ae" == "$OWNER_EMAIL" ]] || { echo "✗ ${sha:0:8}: author $ae"; fail=1; }
  [[ "$ce" == "$OWNER_EMAIL" ]] || { echo "✗ ${sha:0:8}: committer $ce"; fail=1; }
  check_msg "${sha:0:8}" "$(git log -1 --format=%B "$sha")"
done
[[ $fail == 0 ]] && echo "✓ all commits in $range: single contributor $OWNER_EMAIL"
exit $fail
