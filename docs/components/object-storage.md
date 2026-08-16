<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Object Storage — Deployment Guide

The Citra Decision System uses S3-compatible object storage for file uploads (documents, images, audio). Any S3-compatible service works.

## Requirements

- S3-compatible API (MinIO, AWS S3, any S3-compatible endpoint)
- A bucket named `citra-documents` (configurable)

## Option A: AWS S3

1. Create an S3 bucket
2. Create an IAM user with `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` permissions
3. Set in `.env`:
   ```env
   BUCKET_NAME=citra-documents
   BUCKET_ACCESS_KEY=AKIAxxxxxxxxxxxxxxxx
   BUCKET_SECRET_KEY=your-secret-key
   BUCKET_REGION=us-east-1
   # Leave BUCKET_ENDPOINT_URL empty for AWS S3
   BUCKET_ENDPOINT_URL=
   ```

## Option B: MinIO Docker (Single Machine)

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.minio.yml \
  --env-file ../../.env up -d
```

Default credentials:
```env
BUCKET_ENDPOINT_URL=http://localhost:9000
BUCKET_ACCESS_KEY=minioadmin
BUCKET_SECRET_KEY=minioadmin
BUCKET_NAME=citra-documents
```

### Create the bucket (first time only)

```bash
# Using MinIO Client (mc)
docker run --rm --network host --entrypoint sh minio/mc -c \
  "mc alias set citra http://localhost:9000 minioadmin minioadmin; \
   mc mb --ignore-existing citra/citra-documents"
```

Or use the MinIO Console at `http://localhost:9001`.

> When running Citra services in the same Docker Compose network, use `http://minio:9000` instead of `http://localhost:9000`.

## Option C: Self-Managed MinIO

Install MinIO on any server:

```bash
# Download and run
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio
MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin ./minio server /data --console-address ":9001"
```

Point your Citra services to the remote MinIO:
```env
BUCKET_ENDPOINT_URL=http://<minio-host>:9000
BUCKET_ACCESS_KEY=minioadmin
BUCKET_SECRET_KEY=minioadmin
BUCKET_NAME=citra-documents
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BUCKET_ENDPOINT_URL` | For MinIO | `http://minio:9000` | S3-compatible endpoint (leave empty for AWS S3) |
| `BUCKET_NAME` | Yes | `citra-documents` | Bucket name |
| `BUCKET_ACCESS_KEY` | Yes | `minioadmin` | Access key |
| `BUCKET_SECRET_KEY` | Yes | `minioadmin` | Secret key |
| `BUCKET_REGION` | For AWS | `us-east-1` | AWS region |

## Verification

```bash
# MinIO health check
curl http://localhost:9000/minio/health/live

# List buckets (with mc)
mc alias set citra http://localhost:9000 minioadmin minioadmin
mc ls citra/

# Setup validator
./scripts/setup.sh --check
```
