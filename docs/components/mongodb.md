<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# MongoDB — Deployment Guide

MongoDB is the primary document store for the Citra Decision System. All services share a single MongoDB instance (or cluster).

## Requirements

- MongoDB 7.0+ with **replica set** enabled (required for change streams)
- Database: `citra_ai` (configurable via `MONGODB_DATABASE`)

## Option A: MongoDB Atlas (Cloud)

1. Create a free or paid cluster at [cloud.mongodb.com](https://cloud.mongodb.com)
2. Create a database user with read/write access
3. Whitelist your server IP (or use `0.0.0.0/0` for testing)
4. Get the connection string:
   ```
   mongodb+srv://username:password@cluster.mongodb.net/
   ```
5. Set in `.env`:
   ```env
   MONGODB_CONN_STRING=mongodb+srv://username:password@cluster.mongodb.net/
   MONGODB_DATABASE=citra_ai
   ```

## Option B: Docker (Single Machine)

Use the modular compose file:

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.mongodb.yml \
  --env-file ../../.env up -d
```

This starts MongoDB 7.0 as a single-node replica set on port `27017`.

Default credentials (change in `.env`):
```env
MONGODB_CONN_STRING=mongodb://root:citradev@localhost:27017/?authSource=admin&replicaSet=rs0
MONGODB_DATABASE=citra_ai
```

### Initialize Replica Set (first time only)

```bash
docker exec citra-mongodb mongosh --eval "rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27017'}]})"
```

## Option C: Self-Managed Server

Install MongoDB on any Linux server:

```bash
# Ubuntu/Debian
sudo apt-get install -y gnupg curl
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt-get update && sudo apt-get install -y mongodb-org
sudo systemctl start mongod && sudo systemctl enable mongod
```

Enable replica set in `/etc/mongod.conf`:
```yaml
replication:
  replSetName: rs0
```

Then initiate:
```bash
mongosh --eval "rs.initiate()"
```

Set in `.env` on your Citra service machines:
```env
MONGODB_CONN_STRING=mongodb://username:password@<mongodb-host>:27017/?authSource=admin&replicaSet=rs0
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MONGODB_CONN_STRING` | Yes | `mongodb://root:citradev@mongodb:27017/...` | Full connection string |
| `MONGODB_DATABASE` | Yes | `citra_ai` | Database name |

## Verification

```bash
# Test connectivity (replace with your connection string)
mongosh "mongodb://root:citradev@localhost:27017/?authSource=admin&replicaSet=rs0" --eval "db.adminCommand('ping')"

# Or from the Citra setup validator:
./scripts/setup.sh --check
```

## Backup

```bash
# Docker
docker exec citra-mongodb mongodump --out /data/backup --gzip
docker cp citra-mongodb:/data/backup ./mongodb-backup

# Atlas — use Atlas built-in continuous backup

# Self-managed
mongodump --uri="your-connection-string" --out /backup/path --gzip
```
