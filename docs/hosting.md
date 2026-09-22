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
`down.sh` first if the data matters.

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
