<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra AI Reranker Service

Machine learning reranker service for improving search result quality.
Scores and reorders search results from Milvus vector search to improve retrieval accuracy.

## Model Selection (Automatic)

The service **auto-detects GPU availability** and selects the best model:

| Device | Model | Quality | Latency | VRAM / RAM |
|--------|-------|---------|---------|------------|
| **CUDA GPU** (recommended) | `BAAI/bge-reranker-v2-m3` | High | ~15ms/query | ~2 GB VRAM |
| **Apple MPS** | `BAAI/bge-reranker-v2-m3` | High | ~30ms/query | ~2 GB RAM |
| **CPU** (fallback) | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Good | ~80ms/query | ~500 MB RAM |

> **Production recommendation:** Always run on GPU. The GPU model (BGE-reranker-v2-m3) is significantly
> more accurate for multilingual and domain-specific queries. The CPU model (ms-marco-MiniLM) is a
> lightweight English-focused fallback for development or environments without GPU.

If GPU loading fails (driver issues, OOM), the service automatically falls back to the CPU model
and logs a warning. No manual intervention needed.

## Configuration

Uses `.env` file only (no Vault support). Copy `.env.example` to `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `7302` | Server port |
| `HOST` | `0.0.0.0` | Bind address |
| `RERANKER_MODEL_GPU` | `BAAI/bge-reranker-v2-m3` | Model used when GPU is available |
| `RERANKER_MODEL_CPU` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Fallback model for CPU-only |
| `RERANKER_MODEL` | (empty) | Override — force a specific model regardless of device |

## Local Development

```bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
python main.py
```

## Docker

### GPU (Recommended)

```bash
docker build -t reranker-service .
docker run --gpus all -p 7302:7302 reranker-service
```

### CPU (Fallback)

```bash
docker build -t reranker-service .
docker run -p 7302:7302 reranker-service
```

The service detects no GPU and automatically loads the lightweight CPU model.

## API

### Health Check

```
GET /health
```

### Rerank

```
POST /rerank
Content-Type: application/json

{
  "query": "What is the refund policy?",
  "passages": ["Full refund within 30 days...", "No returns on sale items..."],
  "top_k": 5
}
```

## Resource Requirements

### GPU (Production)

- GPU: 1x with ~2 GB VRAM (can share a GPU with other services)
- RAM: 2 GB
- Startup: ~30 seconds

### CPU (Development / Fallback)

- CPU: 4 cores recommended
- RAM: 2 GB
- Startup: ~20 seconds
