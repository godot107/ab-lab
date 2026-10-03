#!/usr/bin/env bash
# Live status of the running POC, without stopping it.
#
#   deploy/stats.sh             # copy the live events.db to data/, print the status brief
#   deploy/stats.sh --counts    # just visitors and drill-throughs per arm (/api/stats)
#   AB_URL=https://ab.example.com deploy/stats.sh --counts   # same, no AWS login needed
#
# Safe to run as often as you like: until the stopping rule is met the brief is
# blinded (pooled usage, no A-vs-B numbers), so checking is not peeking.
set -euo pipefail

STACK=${STACK:-ab-lab}
export AWS_REGION=${AWS_REGION:-us-east-1}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

if [ "${1:-}" = "--counts" ]; then
  curl -fsS "${AB_URL:-$(out SiteUrl)}/api/stats"
  exit 0
fi

BUCKET=$(out ArtifactBucket)
INSTANCE=$(out InstanceId)
EXPERIMENT=$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Parameters[?ParameterKey=='ExperimentName'].ParameterValue" --output text)
dest="$ROOT/data/events-$STACK-live.db"

echo "==> Copying the live events.db (the site keeps running)" >&2
cmd=$(aws ssm send-command --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["/usr/local/sbin/ab-export.sh"]' \
  --query Command.CommandId --output text)
while :; do
  status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in Pending|InProgress|Delayed) sleep 3 ;; *) break ;; esac
done
[ "$status" = "Success" ] || { echo "export failed ($status)" >&2; exit 1; }
aws s3 cp "s3://$BUCKET/events.db" "$dest" --only-show-errors
echo "    saved $dest" >&2

py="$ROOT/.venv/bin/python"
[ -x "$py" ] || py=python3
"$py" "$ROOT/analysis/brief.py" --db "$dest" --experiment "$EXPERIMENT"
