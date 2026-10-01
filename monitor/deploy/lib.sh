# shellcheck shell=bash
# Variables here are used by the scripts that source this file.
# shellcheck disable=SC2034
# Shared setup for the deploy scripts. Sourced, not executed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

if [ ! -f "$HERE/config.env" ]; then
  echo "ERROR: $HERE/config.env missing. Copy config.env.example and edit it." >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$HERE/config.env"

: "${PROJECT:?}" "${DOMAIN:?}" "${EMAIL:?}" "${REGION:?}"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_DEFAULT_REGION="$REGION"

STACK="$PROJECT"
KEY_NAME="$PROJECT-key"
KEY_FILE="$HOME/.ssh/$KEY_NAME"
SSH_OPTS=(-i "$KEY_FILE" -o StrictHostKeyChecking=accept-new)

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

stack_output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
