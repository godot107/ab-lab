#!/usr/bin/env bash
# Check the live site end to end:
#   1. DNS points at the Elastic IP
#   2. HTTPS with a valid Let's Encrypt certificate, HTTP redirects to HTTPS
#   3. the app is up, the data is fresh, and blocking data-quality checks pass
#   4. the app comes back by itself after a reboot   (--reboot only)
#
# Usage: ./deploy/04_verify.sh [--reboot]
source "$(dirname "$0")/lib.sh"

REBOOT=0
[ "${1:-}" = "--reboot" ] && REBOOT=1

BASE="https://$DOMAIN"
PASS=0
FAIL=0

check() {  # check <description> <command...>
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  \033[1;32mPASS\033[0m %s\n' "$what"; PASS=$((PASS + 1))
  else
    printf '  \033[1;31mFAIL\033[0m %s\n' "$what"; FAIL=$((FAIL + 1))
  fi
}

need() { command -v "$1" >/dev/null || die "$1 is required but not installed"; }
need curl; need openssl; need dig; need jq

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

IP="$(stack_output PublicIpAddress)"
[ -n "$IP" ] && [ "$IP" != "None" ] || die "stack '$STACK' has no Elastic IP"

say "DNS"
RESOLVED="$(dig +short "$DOMAIN" A | tail -1)"
[ "$RESOLVED" = "$IP" ] \
  || die "$DOMAIN resolves to '${RESOLVED:-nothing}', expected $IP. Fix the A record and wait for the TTL."
printf '  \033[1;32mPASS\033[0m %s resolves to %s\n' "$DOMAIN" "$IP"; PASS=$((PASS + 1))

say "HTTPS"
check "port 80 redirects to HTTPS" \
  bash -c "curl -sS -o /dev/null -w '%{redirect_url}' http://$DOMAIN/ | grep -q '^https://'"
echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates > "$TMP/cert.txt" 2>/dev/null || true
[ -s "$TMP/cert.txt" ] || die "no certificate returned yet; see: docker compose logs caddy"
sed 's/^/      /' "$TMP/cert.txt"
check "certificate chain validates" curl -sS -o /dev/null "$BASE/healthz"
check "certificate is issued by Let's Encrypt" grep -qi "issuer=.*let's encrypt" "$TMP/cert.txt"
check "certificate is valid for at least 7 more days" \
  bash -c "echo | openssl s_client -servername $DOMAIN -connect $DOMAIN:443 2>/dev/null | openssl x509 -noout -checkend 604800"

say "App and data"
curl -sS "$BASE/healthz" > "$TMP/health.json" || true
sed 's/^/      /' "$TMP/health.json"; echo
check "health endpoint reports ok (all blocking checks pass)" jq -e '.status == "ok"' "$TMP/health.json"
check "data is no more than 14 days old" \
  bash -c "d=\$(jq -r .data_through $TMP/health.json); [ \$(( (\$(date +%s) - \$(date -d \"\$d\" +%s)) / 86400 )) -le 14 ]"
check "dashboard page renders" bash -c "curl -sS $BASE/ | grep -q 'Hiring Demand Monitor'"
check "daily refresh timer is enabled" \
  ssh "${SSH_OPTS[@]}" "ubuntu@$IP" "systemctl is-enabled $PROJECT-refresh.timer"

if [ "$REBOOT" = 1 ]; then
  say "Reboot survival"
  INSTANCE_ID="$(aws cloudformation describe-stack-resource --stack-name "$STACK" \
    --logical-resource-id Instance --query StackResourceDetail.PhysicalResourceId --output text)"
  aws ec2 reboot-instances --instance-ids "$INSTANCE_ID"
  echo "  rebooting $INSTANCE_ID, waiting for the site to answer again"
  BACK=0
  for i in $(seq 1 60); do
    sleep 10
    if curl -sSf --max-time 5 "$BASE/healthz" >/dev/null 2>&1; then BACK=$i; break; fi
    printf '.'
  done
  echo
  check "site answered again after reboot (~$((BACK * 10))s)" test "$BACK" -gt 0
else
  say "Reboot survival: skipped (re-run with --reboot)"
fi

echo
printf '  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
