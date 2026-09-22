#!/usr/bin/env bash
# Bring the POC up on EC2, or redeploy the current code to a running stack.
#
#   deploy/up.sh                                  # plain HTTP on the public IP
#   AB_DOMAIN=ab.example.com ADMIN_EMAIL=me@example.com deploy/up.sh
#   AB_EXPERIMENT=exp001_layout deploy/up.sh      # the real, pre-registered run
#
# Optional: ANTHROPIC_API_KEY in your shell turns the chat box on. It goes to
# SSM Parameter Store as a SecureString -- never into the template or UserData,
# both of which are readable in the AWS console.
#
# Needs: AWS CLI v2 with credentials, a default VPC in the region.
set -euo pipefail

STACK=${STACK:-ab-lab}
export AWS_REGION=${AWS_REGION:-us-east-1}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

echo "==> Stack $STACK in $AWS_REGION"
aws cloudformation deploy \
  --stack-name "$STACK" \
  --template-file "$ROOT/infra/ec2.yaml" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "DomainName=${AB_DOMAIN:-}" "AdminEmail=${ADMIN_EMAIL:-}" \
    "ExperimentName=${AB_EXPERIMENT:-exp001_demo}"

BUCKET=$(out ArtifactBucket)
INSTANCE=$(out InstanceId)

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "==> Storing chat key in Parameter Store"
  aws ssm put-parameter --name "/$STACK/anthropic-api-key" --type SecureString \
    --value "$ANTHROPIC_API_KEY" --overwrite >/dev/null
fi

echo "==> Uploading release"
# Everything git would track (committed or not), minus what .gitignore
# excludes: no .env, no events.db, no venv.
tarball=$(mktemp --suffix=.tgz)
trap 'rm -f "$tarball"' EXIT
(cd "$ROOT" && git ls-files -co --exclude-standard -z | tar -czf "$tarball" --null -T -)
aws s3 cp "$tarball" "s3://$BUCKET/release.tgz" --only-show-errors

echo "==> Waiting for the instance to register with SSM"
for _ in $(seq 60); do
  state=$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE" \
    --query "InstanceInformationList[0].PingStatus" --output text 2>/dev/null || true)
  [ "$state" = "Online" ] && break
  sleep 10
done
[ "$state" = "Online" ] || { echo "instance never came online in SSM" >&2; exit 1; }

echo "==> Deploying (first run waits for bootstrap, then builds the image)"
cmd=$(aws ssm send-command --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["/usr/local/sbin/ab-deploy.sh"],executionTimeout=["1500"]' \
  --query Command.CommandId --output text)
while :; do
  status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in
    Pending|InProgress|Delayed) sleep 10 ;;
    *) break ;;
  esac
done
aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
  --query StandardOutputContent --output text | tail -8
[ "$status" = "Success" ] || {
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query StandardErrorContent --output text >&2
  echo "deploy failed ($status)" >&2; exit 1; }

echo
echo "Site:     $(out SiteUrl)"
if [ -n "${AB_DOMAIN:-}" ]; then
  echo "DNS:      $(out DnsRecord)   <- set this, then give Caddy a minute"
fi
echo "Shell:    $(out ShellCommand)"
echo "Teardown: deploy/down.sh   (collects events.db first)"
