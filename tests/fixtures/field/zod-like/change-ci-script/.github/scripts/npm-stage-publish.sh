#!/usr/bin/env bash
set -euo pipefail
# verbose: a failed OIDC token exchange is only reported at that level
npm stage publish --loglevel verbose --tag "$1"
