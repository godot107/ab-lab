#!/usr/bin/env bash
# Create an EC2 key pair and save the private key to ~/.ssh. Safe to re-run.
source "$(dirname "$0")/lib.sh"

if aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  [ -f "$KEY_FILE" ] || die "key pair '$KEY_NAME' exists in AWS but $KEY_FILE is missing; delete it in the console and re-run"
  say "Key pair '$KEY_NAME' already exists ($KEY_FILE)"
  exit 0
fi

say "Creating key pair '$KEY_NAME' in $REGION"
mkdir -p "$HOME/.ssh"
umask 077
aws ec2 create-key-pair --key-name "$KEY_NAME" --key-type ed25519 \
  --query KeyMaterial --output text > "$KEY_FILE"
chmod 600 "$KEY_FILE"
echo "  private key saved to $KEY_FILE"
