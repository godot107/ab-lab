#!/usr/bin/env bash
# Remote commands deliberately expand $PROJECT locally before sending.
# shellcheck disable=SC2029
# Ship the app to the instance and (re)start it. Safe to re-run on every change.
source "$(dirname "$0")/lib.sh"

IP="$(stack_output PublicIpAddress)"
[ -n "$IP" ] && [ "$IP" != "None" ] || die "stack '$STACK' has no Elastic IP yet"
REMOTE="ubuntu@$IP"

say "Waiting for first-boot provisioning (about 3 minutes on a new instance)"
for i in $(seq 1 60); do
  if ssh "${SSH_OPTS[@]}" -o ConnectTimeout=5 "$REMOTE" 'test -f /var/lib/bootstrap-complete' 2>/dev/null; then
    echo "  bootstrap complete"
    break
  fi
  [ "$i" = 60 ] && die "bootstrap did not finish; check /var/log/bootstrap.log on the host"
  printf '.'
  sleep 10
done

say "Syncing application files to /opt/$PROJECT"
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude '__pycache__' --exclude 'var/' --exclude '*.db' \
  "$ROOT/app" "$REMOTE:/opt/$PROJECT/"
rsync -az -e "ssh ${SSH_OPTS[*]}" "$ROOT/compose.yaml" "$REMOTE:/opt/$PROJECT/compose.yaml"

say "Building and starting containers (first start downloads the data)"
ssh "${SSH_OPTS[@]}" "$REMOTE" "sudo systemctl restart $PROJECT.service"
ssh "${SSH_OPTS[@]}" "$REMOTE" "cd /opt/$PROJECT && docker compose ps"

say "Checking the app from inside the instance"
for i in $(seq 1 30); do
  if ssh "${SSH_OPTS[@]}" "$REMOTE" "cd /opt/$PROJECT && docker compose exec -T web \
       python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8050/healthz').read().decode())\"" 2>/dev/null; then
    break
  fi
  [ "$i" = 30 ] && die "app did not become healthy; see: docker compose logs web"
  sleep 5
done

echo
echo "  Public URL: https://$DOMAIN   (certificate issues ~30s after DNS resolves to $IP)"
echo "  Then run:   ./deploy/04_verify.sh"
