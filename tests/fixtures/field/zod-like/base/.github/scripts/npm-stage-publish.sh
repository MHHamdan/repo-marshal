#!/usr/bin/env bash
set -euo pipefail
npm stage publish --tag "$1"
