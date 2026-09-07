<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# Backups

What holds state, how to copy it by hand, and the shipped backup image that
does it on a schedule, encrypts it, and proves every month that it can be
restored. **Nothing is backed up by default.** A quickstart install keeps its
data in Docker volumes and nowhere else until you set one of the two
approaches below up.

## What holds state

| Volume | Container | Holds |
|---|---|---|
| `mongodb_data` | `citra-mongodb` | apps, specs, users, orgs, departments, the decision ledger, learned judgements, corrections |
| `milvus_data`, `milvus_etcd_data`, `milvus_minio_data` | `citra-milvus` and its etcd and MinIO | the SOP vector index |
| `minio_data` | `citra-minio` | uploaded documents and generated files, bucket `citra-documents` |
| `redis_data`, `queue_redis_data` | `citra-redis`, `queue-redis` | cache and the job queue; safe to lose, rebuilt on restart |
| `*-pgdata` | `citra-postgres` | the demo tenant's system of record |

Volume names carry the Compose project prefix on disk. `docker volume ls`
shows the exact names.

If you can protect only one thing, protect Mongo. The vector index can be
rebuilt by re-ingesting the SOP library, and the demo Postgres by re-seeding.
The ledger and the judgements cannot be reconstructed from anywhere.

`make down` keeps every volume. `make down ARGS=-v` deletes them all with no
second prompt.

## By hand

Good enough for an evaluation, and the fallback when the sidecar is not set up.

**MongoDB**

```bash
docker exec citra-mongodb mongodump --gzip --archive=/tmp/citra.archive.gz
```

```bash
docker cp citra-mongodb:/tmp/citra.archive.gz ./citra-backup.archive.gz
```

Restore with `mongorestore --gzip --archive=... --drop` inside the container.

**MinIO documents**

```bash
mc alias set citra http://localhost:9000 minioadmin minioadmin
```

```bash
mc mirror citra/citra-documents ./minio-backup
```

Reverse the arguments to restore.

**Milvus** uses the
[milvus-backup](https://github.com/zilliztech/milvus-backup) tool. Or skip it
and re-ingest.

**Learned memory on its own.** Every app can export its four golden
collections -- decision records, item decisions, clauses, corrections -- as
gzipped JSONL, either as a download from `GET /apps/{slug}/memory/export` or
pushed incrementally to a bucket you own with
`POST /apps/{slug}/memory/export/run`. This is the portable copy of what your
officers taught the app, independent of the database format, and the one to
keep even when you have full volume backups.

## The backup sidecar

[`infrastructure/dept-stack/backup/`](https://github.com/Trustedwear-Tech/citra-decision-system/tree/main/infrastructure/dept-stack/backup)
is a small Alpine image that runs beside the stack and, every six hours by
default:

1. `mongodump` of the whole database to a gzipped archive
2. a Milvus snapshot through `milvus-backup`, into the MinIO that Milvus owns
3. `restic backup` of the archive, the snapshot manifest and `/etc/citra` to an
   S3 bucket you control -- encrypted, deduplicated, incremental
4. `restic forget --prune` to apply retention: 24 hourly, 30 daily, 52 weekly

It publishes metrics through the node-exporter textfile collector --
`dept_backup_last_success_timestamp_seconds`, per-stage durations and sizes --
and the shipped alert rules fire `DeptBackupStale` when the last success is
too old. See [Observability](Observability).

**Build it:**

```bash
docker build -t citra-backup infrastructure/dept-stack/backup
```

**Run it** against a quickstart install. Put the settings in a file, say
`backup.env`, so the restore and drill commands below can reuse them:

```
DEPT_ID=main
MONGO_URI=mongodb://root:password@citra-mongodb:27017/?authSource=admin&replicaSet=rs0
MILVUS_URI=http://citra-milvus:19530
MILVUS_HOST=citra-milvus
MILVUS_MINIO_HOST=citra-milvus-minio
BACKUP_S3_ENDPOINT=s3.example.com
BACKUP_S3_BUCKET=citra-backups
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
BACKUP_RESTIC_PASSWORD=a-long-random-passphrase
```

```bash
docker run -d --name citra-backup --network citra-network --restart unless-stopped --env-file backup.env citra-backup
```

Every variable is required and the script refuses to start without one. The
Mongo credentials are whatever you set in `.env`; the Milvus MinIO ones default
to `minioadmin` if you kept the shipped values. `BACKUP_INTERVAL` (seconds) and
the three `BACKUP_RETENTION_*` counts are the tunables. If the first run
fails, the container stays up and says so in its logs rather than exiting, so
check `docker logs citra-backup` once after starting it.

**Keep the restic passphrase somewhere other than the box.** A restic
repository without its passphrase is noise. It is the one secret whose loss
turns every backup into nothing.

The bucket itself must be one that a compromised host cannot delete from: a
separate account, object lock, or at least credentials scoped to write and
list. A backup the attacker can erase is not a backup.

## Restoring

```bash
docker run --rm -it --network citra-network --env-file backup.env citra-backup restore.sh latest
```

`restore.sh` takes a restic snapshot id or `latest`, pulls it down, runs
`mongorestore --drop` and then `milvus-backup restore`. It prints each step
before running it. `--drop` replaces the live collections: stop the app
services first, or you restore under a running writer.

## The restore drill

A backup you have never restored is a hope. `restore-drill.sh` verifies the
latest snapshot without touching the live databases:

1. `restic check --read-data-subset=5%` -- repository integrity, and a five
   percent sample of blocks actually read back and decrypted
2. restore the latest snapshot to a scratch directory
3. assert the Mongo archive is present and not suspiciously small
4. assert the Milvus manifest parses

On success it writes `dept_backup_restore_test_success_timestamp`; on failure
it exits non-zero and leaves the old timestamp in place, so the
`DeptBackupRestoreDrillStale` alert fires. Run it monthly from cron or a
scheduler, with the same environment as the sidecar:

```bash
docker run --rm --network citra-network --env-file backup.env citra-backup restore-drill.sh
```

## What is not covered

There is no point-in-time restore. Recovery is to the last snapshot, so with
the default six-hour interval you can lose up to six hours of decisions.
Shorten `BACKUP_INTERVAL` if that is too long; the incremental push makes
frequent runs cheap. Redis is deliberately not backed up. The memory export
above is the only cross-version, cross-database copy of what was learned, and
is worth its own schedule.
