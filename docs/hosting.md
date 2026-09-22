# Hosting: EC2 proof of concept

## What runs where

One EC2 instance, created and deleted by CloudFormation (`infra/ec2.yaml`):

| Resource | Why |
|---|---|
| `t4g.small` (Graviton, 2 GB), Ubuntu 24.04 | Runs Docker Compose: Caddy (TLS) + the Flask app. 2 GB builds the image without swapping. |
| Launch template, IMDSv2 required | Hardens instance metadata; also the exact thing an Auto Scaling group would reuse. |
| Security group: 80, 443 (TCP + UDP) | No port 22. Shell access is SSM Session Manager. |
| IAM role | SSM core + read/write on its own artifact bucket + read its own `/<stack>/*` parameters. |
| Private S3 bucket | Carries the release in and `events.db` out. Encrypted, public access blocked, objects expire in 14 days. |
| SSM Parameter Store SecureString (created by `up.sh`, not the stack) | The Anthropic key, if you set one. Kept out of the template and UserData, which the AWS console shows in plain text. |

No Elastic IP: the stack lives for one demo, so the auto-assigned public IPv4
is stable for its whole life. Requires a default VPC in the region.

## Lifecycle

```bash
deploy/up.sh                                      # plain HTTP on the public IP
AB_DOMAIN=ab.example.com ADMIN_EMAIL=me@example.com deploy/up.sh   # HTTPS
ANTHROPIC_API_KEY=sk-ant-... deploy/up.sh         # also turns the chat box on
```

`up.sh` creates or updates the stack, uploads the working tree (everything
`.gitignore` doesn't exclude) to S3, and runs the deploy on the instance
through SSM. Rerun it to ship code changes. With a domain, set the printed A
record; Caddy retries until DNS resolves, then gets its certificate.

```bash
deploy/down.sh
```

`down.sh` exports `events.db` to `data/events-<stack>-<timestamp>.db`, empties
the bucket, deletes the stack and the key parameter. **Delete, don't stop**: a
stopped instance still bills its EBS volume, and it's easy to forget.

Changing `infra/ec2.yaml` itself (not the app) can make CloudFormation replace
the instance, which starts a fresh `events.db` and a fresh `AB_SALT`. Run
`down.sh` first if the data matters. **Changing `AB_DOMAIN` between runs
counts**: the domain is baked into the instance's UserData, so it forces a
replacement too, and with it a new public IP. See design considerations below.

### Pointing a domain at it (Spaceship DNS)

Decide on the domain **before the first `up.sh`**, not after:

1. `AB_DOMAIN=ab.example.com ADMIN_EMAIL=admin@example.com deploy/up.sh`.
   It prints `PublicIp` and the A record to create.
2. In Spaceship: Domains → your domain → DNS records → add an **A** record,
   host `ab`, value the printed IP, lowest TTL offered.
3. `dig +short ab.example.com` until it returns that IP. Caddy keeps retrying
   the certificate in the meantime, and the site comes up on
   `https://ab.example.com` once DNS resolves.
4. After the demo: `deploy/down.sh`, **then delete the A record.** The IP goes
   back to AWS's pool; a record left behind points your name at whoever gets
   it next.

To check the instance works before DNS resolves, run
`curl -sI -H "Host: ab.example.com" http://<PublicIp>`. A `308` redirect to
HTTPS means Caddy is serving that host.

Without a domain (`deploy/up.sh` alone), the site is plain HTTP on the public
IP, which is enough for a screen-shared demo.

## Design considerations: deliberately not built

Two changes would make the "confirm by IP first, add the domain later" flow
work. For a days-long demo, the auto-assigned IP and choosing the domain up
front are simpler, so both are recorded here rather than implemented.

### 1. Elastic IP instead of the auto-assigned public IP

**Problem.** The auto-assigned IPv4 belongs to the instance. It changes
whenever the instance is replaced (any UserData or launch-template change,
including the domain) or stopped and started. A DNS record pointing at the old
address silently stops working.

**Change.** Add an `AWS::EC2::EIP` associated with the instance, and output it
as `PublicIp`. The address then survives replacement and stop/start, so the DNS
record is set once for the stack's whole life.

**Cost.** None extra while it's attached: since February 2024 AWS bills every
public IPv4 at $0.005/hr, auto-assigned or elastic. An EIP bills while idle
too, so it has to be deleted with the stack, which `down.sh` would already do.

**Why not now.** The demo stack is created once and deleted after, so its
auto-assigned IP is stable for its whole life, as long as nothing in the
template changes mid-demo.

### 2. Apply the domain at deploy time, not at instance creation

**Problem.** `AB_DOMAIN`, `ADMIN_EMAIL` and `COOKIE_SECURE` are written into
`.env` by UserData on first boot. Changing the domain changes UserData, which
replaces the instance: new IP, new `AB_SALT` (every returning visitor
re-bucketed) and an empty `events.db`.

**Change.** Remove the domain from UserData. `up.sh` passes it to
`ab-deploy.sh` through the SSM command, which rewrites those three lines of
`.env` on every deploy and leaves `AB_SALT` alone. `DomainName` stays a stack
parameter only for the `SiteUrl` and `DnsRecord` outputs, and outputs never
trigger replacement. Adding or changing the domain becomes a redeploy: same
instance, same IP, same salt, same data, with Caddy picking up the new site
address on restart.

**Why not now.** The demo picks its domain before the first `up.sh`, so it
never changes on a live stack.

**Together** they allow: deploy on the bare IP, confirm the dashboard, create
the DNS record, redeploy with the domain, and have HTTPS with no replacement
and no data loss. A long-running deployment, like the full 28-day run, should
have both.

## Getting at `events.db`

It lives in the `ab-lab_events` Docker volume on the instance:
`/var/lib/docker/volumes/ab-lab_events/_data/events.db`.

**Download a copy any time** (consistent snapshot of the live db; the app
keeps running):

```bash
STACK=ab-lab
ID=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucket'].OutputValue" --output text)
CMD=$(aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript \
  --parameters 'commands=["/usr/local/sbin/ab-export.sh"]' --query Command.CommandId --output text)
aws ssm wait command-executed --command-id $CMD --instance-id $ID
aws s3 cp s3://$BUCKET/events.db data/events-snapshot.db
python analysis/report.py --db data/events-snapshot.db --experiment exp001_demo
```

**Query it in place** from a shell on the instance:

```bash
aws ssm start-session --target $ID            # needs the Session Manager plugin
sudo sqlite3 /var/lib/docker/volumes/ab-lab_events/_data/events.db \
  "SELECT variant, event, COUNT(*) FROM events GROUP BY 1, 2;"
```

**Counts without any AWS access:** `curl <site>/api/stats` returns exposed and
converted per arm, deliberately with no p-value.

**At teardown:** `down.sh` does the export for you before deleting anything.

## Cost

Approximate us-east-1 on-demand rates; check current pricing.

| Item | Per hour | 3-day demo |
|---|---|---|
| `t4g.small` | ~$0.017 | ~$1.20 |
| Public IPv4 | $0.005 | ~$0.36 |
| 16 GB gp3 | ~$0.002 | ~$0.13 |
| S3, SSM, data transfer at demo scale | ~0 | ~0 |
| Chat (optional) | per question, ~$0.02 max | capped by `CHAT_DAILY_LIMIT` (default 100/day) |

A few dollars total, and zero once `down.sh` has run.

`infra/` is scanned statically by CloudBurn in CI (`cloudburn scan infra
--config .cloudburn.yml`). It flags one medium finding on purpose: the
artifact bucket has no storage-class transitions, because its objects expire
before any cheaper tier would pay back the transition fee. The cost risk a
static scan can't see is forgetting to run `down.sh`; that stays a checklist
item.

## Why single-instance, and the path to scale out

The event store is SQLite on the instance's disk. That is the right size for
this project and the reason it doesn't sit behind a load balancer: with two
instances, one visitor's exposure and their clicks could be written to
different databases, and SRM and every rate metric would be computed on split
data. The chat quotas would stop being shared, too.

Scaling out is a known sequence, none of which is needed here:

1. **Shared store first.** Move `store.py` and the chat quota table to RDS
   Postgres (or DynamoDB). Assignment is already stateless -- a hash of the
   cookie -- so any instance buckets a visitor the same way.
2. **ALB + ACM** in front, replacing Caddy for TLS. No sticky sessions needed,
   for the same reason.
3. **Auto Scaling group** from the existing launch template, with the ALB
   health check pointed at `/healthz`.
4. **Release from an image** (ECR) rather than building on each instance.

At production scale the collector itself would usually be replaced too: an
experimentation platform (GrowthBook, Statsig, Optimizely) or a warehouse
event pipeline. See `docs/telemetry_options.md`.

## For a full 28-day run

The pre-registered run can't pause: downtime drops traffic from the sample
and, if it follows a daily pattern, biases who gets measured. Same stack,
kept up for the window: `AB_EXPERIMENT=exp001_layout deploy/up.sh`. Cost is roughly
$16 for the month. Run `report.py` once when the stopping rule is met, then
`down.sh`.
