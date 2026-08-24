<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Contributing to Citra AI

Thank you for your interest in contributing! This document provides guidelines
for contributing to the Citra AI platform.

## Why Contribute to Citra AI?

Your data is your real power — it should never leak to train someone else's
model. Contributing to Citra AI means building an AI platform where data
sovereignty is non-negotiable. We're proving that AI can be powerful AND
private — automation, analytics, generation, research, and browsing with AI
summarization, all without compromising your data.

Join a growing community of developers and enterprises building the future of
private AI.

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

## Contributor License Agreement

By submitting a pull request, you agree to our [CLA](CLA.md). This grants
Citra AI the right to use your contribution under the project's license terms.

## Getting Started

### Prerequisites

- **Docker** and **Docker Compose** (v2.20+)
- **Git** with GPG/SSH signing configured
- **Python 3.11+** (for Citra-Service, reranker, playwright-render,
  duckdb-query)
- **Node.js 18+** (for Citra-UI, user-service)

### Local Development Setup

```bash
# Clone the repository
git clone https://github.com/Trustedwear-Tech/citra-decision-system.git
cd citra

# Run the setup script (creates .env, starts infrastructure)
./scripts/setup.sh

# Or manually:
cp .env.example .env
docker compose -f docker-compose.infra.yml up -d   # databases (Mongo, Redis, Milvus, MinIO)
# Application services run from their own project folders (per-service compose
# files / dev setup) — see LOCAL_DOCKER_DEV.md for the full local dev stack.

# Start individual services for development
cd Citra-Service
python -m venv venv && source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8085
```

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/Trustedwear-Tech/citra-decision-system/issues)
- Include: steps to reproduce, expected vs actual behavior, environment details
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

### Submitting Changes

1. **Fork** the repository
2. **Create a branch** from `main`: `git checkout -b feat/your-feature`
3. **Make your changes** following the coding standards below
4. **Add license headers** to new files (run `./scripts/add-headers.sh`)
5. **Test** your changes locally
6. **Commit** with a descriptive message (signed commits preferred)
7. **Push** and open a **Pull Request** against `main`

### Branch Naming

- `feat/description` — New features
- `fix/description` — Bug fixes
- `docs/description` — Documentation
- `refactor/description` — Code restructuring
- `infra/description` — Infrastructure changes

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(citra-service): add MinIO storage backend
fix(user-service): handle expired JWT gracefully
docs: update deployment guide for Docker Compose
```

## Coding Standards

### Python (Services)

- **Formatter**: Black (line length 100)
- **Linter**: Ruff
- **Type hints**: Encouraged for public APIs
- **Docstrings**: Required for public functions
- **License header**: Required on all files

### JavaScript/TypeScript (UI, User Service)

- **Formatter**: Prettier
- **Linter**: ESLint
- **License header**: Required on all files

### General

- No hardcoded secrets, API keys, or credentials
- Use environment variables for all configuration
- Write meaningful error messages
- Keep functions focused and reasonably sized

## License Headers

Every source file we wrote carries the Apache-2.0 notice. You do not write it by
hand -- the tool stamps whatever is missing and is safe to re-run:

```bash
./scripts/add-headers.sh            # stamp what is missing
./scripts/add-headers.sh --check    # report only; this is what CI runs
```

Better still, let it happen before you commit:

```bash
pip install pre-commit && pre-commit install
```

The hook stamps your staged files and aborts the commit so you re-stage them --
the same flow as a formatter. The `license headers` workflow enforces the same
check on every push and pull request.

Header format for Python (`#` comments -- shell, YAML and Dockerfiles are the
same; JavaScript and TypeScript use `//`, SQL uses `--`, and CSS, HTML and
Markdown use a block comment):

```python
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
```

Three things about that header are deliberate:

- **The copyright holder is the company**, not an individual. The author is
  named on his own line, so attribution does not create a second, competing
  ownership claim against the CLA you signed.
- **`Apache-2.0` is the registered SPDX identifier**, which licence scanners
  already recognise. Earlier versions of this header said `PROPRIETARY - all
  rights reserved`, and later `BUSL-1.1`; both contradicted the grant that was
  actually in `LICENSE` at the time. Contradictory statements of terms are an
  invitation to argue the grant is ambiguous, so do not reintroduce one.
- **The grant is restated in the file**, not just referenced. A file copied out
  of this repository carries its own terms; a pointer to `LICENSE` does not
  survive copy-paste.

The stamper takes its file list from `git ls-files`. If a file is not tracked,
it is not ours and it is never touched -- that is what keeps our notice off
vendored and third-party code, which matters far more than getting it onto
ours.

## Pull Request Process

1. Ensure all CI checks pass
2. Update documentation if applicable
3. At least one maintainer review required
4. Squash merge preferred for feature branches

## Questions?

- Open a [Discussion](https://github.com/Trustedwear-Tech/citra-decision-system/discussions)
- Email: oss@citra-ai.com
