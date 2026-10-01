#!/usr/bin/env bash
# Delete everything the deploy scripts created, then prove nothing billable is left.
# A stopped EC2 instance still bills for its EBS volume and Elastic IP, so delete, don't stop.
#
# Usage: ./deploy/99_teardown.sh [--yes] [--keep-key]
source "$(dirname "$0")/lib.sh"

ASSUME_YES=0
KEEP_KEY=0
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --keep-key) KEEP_KEY=1 ;;
    *) die "unknown option: $arg" ;;
  esac
done

ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
warn() { printf '  \033[1;33mWARN\033[0m %s\n' "$*"; }

say "Checking AWS credentials"
aws sts get-caller-identity --query Arn --output text \
  || die "AWS CLI is not authenticated for profile '$AWS_PROFILE'"

STACK_EXISTS=0
IP=""
if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  STACK_EXISTS=1
  IP="$(stack_output PublicIpAddress)"
  [ "$IP" = "None" ] && IP=""
fi
KEY_EXISTS=0
aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1 && KEY_EXISTS=1

say "This will permanently delete, in $REGION:"
if [ "$STACK_EXISTS" = 1 ]; then
  echo "  - CloudFormation stack '$STACK': EC2 instance, its volume, security group, Elastic IP ${IP:-(none)}"
else
  echo "  - (stack '$STACK' does not exist, nothing to delete)"
fi
[ "$KEY_EXISTS" = 1 ] && [ "$KEEP_KEY" = 0 ] && echo "  - EC2 key pair '$KEY_NAME' (local $KEY_FILE is kept)"
echo "  The site https://$DOMAIN goes offline."

if [ "$ASSUME_YES" = 0 ]; then
  printf '\nType the project name (%s) to continue: ' "$PROJECT"
  read -r answer || answer=""
  [ "$answer" = "$PROJECT" ] || die "aborted, nothing was deleted"
fi

if [ "$STACK_EXISTS" = 1 ]; then
  say "Deleting stack '$STACK'"
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK" \
    || die "stack deletion failed; see: aws cloudformation describe-stack-events --stack-name $STACK"
  ok "stack deleted"
fi
if [ "$KEY_EXISTS" = 1 ] && [ "$KEEP_KEY" = 0 ]; then
  aws ec2 delete-key-pair --key-name "$KEY_NAME" && ok "key pair deleted"
fi

say "Checking nothing billable is left (tag project=$PROJECT)"
LEFT_I="$(aws ec2 describe-instances --filters "Name=tag:project,Values=$PROJECT" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" --query 'length(Reservations[].Instances[])')"
LEFT_IP="$(aws ec2 describe-addresses --filters "Name=tag:project,Values=$PROJECT" --query 'length(Addresses)')"
if [ "$LEFT_I" = 0 ]; then ok "no instances"; else warn "$LEFT_I instance(s) still tagged $PROJECT"; fi
if [ "$LEFT_IP" = 0 ]; then ok "no Elastic IPs"; else warn "$LEFT_IP Elastic IP(s) still tagged $PROJECT"; fi

cat <<NOTE

  Remove the DNS A record for $DOMAIN: a released Elastic IP can be reassigned to
  another AWS customer, and your hostname would then point at their server.
NOTE
