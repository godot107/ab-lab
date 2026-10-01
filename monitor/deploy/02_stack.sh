#!/usr/bin/env bash
# Create or update the CloudFormation stack (EC2 + Elastic IP), then print next steps.
source "$(dirname "$0")/lib.sh"

if command -v cfn-lint >/dev/null; then
  say "Linting template"
  cfn-lint "$ROOT/infra/ec2.yaml" || die "template failed lint"
else
  say "Skipping lint (cfn-lint not installed; pip install cfn-lint)"
fi

say "Deploying stack '$STACK' to $REGION"
aws cloudformation deploy \
  --stack-name "$STACK" \
  --template-file "$ROOT/infra/ec2.yaml" \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
      ProjectName="$PROJECT" \
      DomainName="$DOMAIN" \
      AdminEmail="$EMAIL" \
      KeyPairName="$KEY_NAME" \
      InstanceType="${INSTANCE_TYPE:-t4g.small}" \
      SshCidr="${SSH_CIDR:-0.0.0.0/0}"

IP="$(stack_output PublicIpAddress)"

cat <<SUMMARY

  Elastic IP : $IP
  SSH        : ssh -i $KEY_FILE ubuntu@$IP

  Next: point DNS at the Elastic IP, then wait for it to resolve.

      Type  Name                 Value           TTL
      A     $DOMAIN    $IP    300

  If the zone is hosted in Lightsail DNS (its API lives in us-east-1):

      aws lightsail create-domain-entry --region us-east-1 \\
        --domain-name ${DNS_ZONE:-<your-zone>} \\
        --domain-entry name=$DOMAIN,type=A,target=$IP

  Check with:  dig +short $DOMAIN
SUMMARY
