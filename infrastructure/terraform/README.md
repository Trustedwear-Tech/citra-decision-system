<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra per-tenant provisioning (Terraform)

Provisions a **dedicated, single-tenant** Citra environment on AWS. One module
call = one customer's environment.

## Topology decision: compose-on-EC2, **not** Kubernetes (for now)

This module Terraform-izes the topology we already run in prod rather than
introducing Kubernetes:

```
  Route53/ACM ──▶ AWS ALB (multi-AZ) ──▶ EC2 box × N ──▶ Traefik ──▶ docker-compose services
```

Why not K8s at this stage:

- **Single-tenant** — one org per deployment. K8s exists to schedule many
  workloads across many nodes; there's nothing here for it to orchestrate that
  Traefik + compose isn't already doing.
- **Reuse a proven runtime** — the compose stack + Traefik + GHCR/SSM deploy are
  tested in prod. K8s would mean authoring + maintaining Helm charts for ~20
  services and operating EKS, for zero functional gain at one-tenant scale.
- **Speed + cost** — an EC2+compose module stands a customer up fast and cheap;
  it keeps the "one week to live" promise.

**The one trade-off** — a single box is a SPOF. Cover it *without* K8s via
`box_count = 2..3` across AZs (the prod shape), plus snapshots + a tested restore
(Wave 3 backup/DR).

**Revisit K8s** only when a contract demands a strict HA/uptime SLA, the fleet
grows enough that chart maintenance pays for itself, or a security review
requires it — not before.

## What it provisions

VPC + multi-AZ public subnets → ALB (+ ACM/Route53 when a zone is delegated) →
`box_count` EC2 boxes (Traefik + the compose stack, SSM-managed) → per-tenant S3
buckets (media / memory-export / artifacts) → IAM.

The boxes are **prepared** by Terraform (Docker, Traefik config, the rendered
per-service replica override); the existing **GHCR + SSM deploy flow ships the
code** (`deploy.ps1`). Terraform owns infra; the deploy owns images/secrets.

## Configurable instance counts

`var.service_replicas` overrides the per-service defaults in
[`modules/citra-tenant/services.tf`](modules/citra-tenant/services.tf). The
catalogue already runs the hot path at **2** — `citra-service`,
`smart-app-service`, `citra-app-runtime`, `citra-workflow`, `citra-worker` —
and everything else at **1**. Set a service to `0` to disable it for a tenant.
Terraform renders those counts into a compose override (`deploy.replicas`);
Traefik / docker-DNS load-balances across the replicas.

**Excluded** (parked / future): `action-chat-service`,
`citra-action-chat-sandbox`. `collaboration-server` is gone entirely (removed
2026-08-09 — unused, never wired into any live Citra-UI feature).
**Kept**: `action-sandbox-host` (the smart-app builder sandboxes depend on it).

## Layout

```
modules/citra-tenant/   reusable per-customer module
  versions.tf  variables.tf  services.tf   # contract + service catalogue
  network.tf   edge.tf       compute.tf  storage.tf  outputs.tf
  templates/   user_data.sh.tftpl  compose.override.yml.tftpl
envs/<customer>/         one dir per deployment (module call + tfvars + remote state)
```

## Usage

```bash
cd envs/acme-power
terraform init
terraform validate
terraform plan     # needs AWS creds for the tenant account
terraform apply
```

> **Skeleton status:** HCL authored to standard AWS-provider patterns but NOT
> yet run through `terraform validate`/`fmt` (no CLI in the authoring env) — run
> both before trusting a `plan`. The DNS/TLS (ACM) block in `edge.tf` is the most
> likely to need a tweak (count-gated cert-validation is a known-fiddly area).
> Before a real `apply`, also wire the per-tenant remote-state backend,
> `admin_ingress_cidrs`, `ssh_key_name`, and confirm box sizing. A private-subnet
> + NAT split and an observability-stack sub-module are follow-on hardening.
