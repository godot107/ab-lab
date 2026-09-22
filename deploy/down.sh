#!/usr/bin/env bash
# Collect events.db, then delete everything the POC created.
#
#   deploy/down.sh
#
# Deleting the stack, not stopping the instance: a stopped instance still
# bills its disk (and would bill its public IPv4 if it had an Elastic IP).
set -euo pipefail

STACK=${STACK:-ab-lab}
export AWS_REGION=${AWS_REGION:-us-east-1}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

BUCKET=$(out ArtifactBucket)
INSTANCE=$(out InstanceId)
dest="$ROOT/data/events-$STACK-$(date -u +%Y%m%dT%H%MZ).db"

echo "==> Exporting events.db"
cmd=$(aws ssm send-command --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["/usr/local/sbin/ab-export.sh"]' \
  --query Command.CommandId --output text)
while :; do
  status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in Pending|InProgress|Delayed) sleep 5 ;; *) break ;; esac
done

if [ "$status" = "Success" ]; then
  aws s3 cp "s3://$BUCKET/events.db" "$dest" --only-show-errors
  echo "    saved $dest"
else
  echo "    export failed ($status)."
  read -r -p "    Delete the stack anyway and lose the data? [y/N] " ok
  [ "$ok" = "y" ] || exit 1
fi

echo "==> Emptying the artifact bucket (a non-empty bucket blocks stack deletion)"
aws s3 rm "s3://$BUCKET" --recursive --only-show-errors

echo "==> Deleting stack $STACK"
aws cloudformation delete-stack --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --stack-name "$STACK"
aws ssm delete-parameter --name "/$STACK/anthropic-api-key" 2>/dev/null || true

echo "Done. Nothing billable is left from this stack."
if [ -f "$dest" ]; then
  echo "Readout: python analysis/report.py --db $dest --experiment ${AB_EXPERIMENT:-exp001_demo}"
fi
