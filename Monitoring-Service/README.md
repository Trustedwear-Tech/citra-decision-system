<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Monitoring-Service

A proactive monitoring system for Citra AI services. Watches application logs, tracks server resource health, and monitors Docker container state — sending detailed forensic alerts with zero autonomous action.

> **Roadmap:** We plan to migrate to **Prometheus + Grafana** for metrics collection, dashboarding, and alerting in a future release. The current implementation provides a lightweight, zero-dependency monitoring solution suitable for single-node deployments.

---

## 🚀 Key Capabilities

### 🔍 Real-time Log Monitoring
- Monitors PROD & TEST services continuously
- Alerts on:
  - `ERROR`, `CRITICAL`, `Exception`, `Traceback`
  - Serious warnings via configurable keywords (e.g. `not authorized`, `Could not check database`)
- Only alerts on **new** log lines (no replay after restart)
- Handles log **truncation and rotation** safely
- Per-error-pattern cooldown:
  - Each unique error message generates one alert per cooldown window
  - Repeated identical errors are suppressed

---

### 🧠 Smart Alerting Mechanism

| Alert Type               | Includes CPU/Mem Snapshot? | Cooldown Key                         |
|--------------------------|----------------------------|--------------------------------------|
| Log Error Alerts         | ✅ Yes                     | (log file, type, error message)      |
| Docker Unhealthy Alerts  | ✅ Yes                     | (container name, type)               |
| System Resource Alerts   | ❌ No (already CPU/Mem)    | (source="system", type="resource")   |

- Delivered via configurable alert transport: **log** (default), **SMTP**, **AWS SES**, or **webhook**
- Avoids alert storms from noisy logs or unstable services
- All alert decisions are logged for audit and debugging

---

### 🖥 System Resource Monitoring

- Monitors CPU & Memory usage at a configurable interval
- Triggers alert only when:
  - Usage is above threshold (e.g. 85%)
  - AND stays high for a sustained duration (e.g. 60 seconds)
- Prevents false alarms on short spikes
- Provides clear system health visibility

---

### 🐳 Docker Container Health Monitoring

> **MONITOR + REPORT ONLY — no automatic restarts or container mutations.**

- Detects containers entering `unhealthy` healthcheck state
- Detects **external restarts** (by Docker engine, deploy, or manual action) and classifies the likely cause:
  - OOM Kill (`OOMKilled=true`)
  - SIGKILL (ExitCode=137) / SIGTERM (ExitCode=143)
  - Application crash (ExitCode=1)
  - Host reboot (StartedAt ≤ host boot time)
  - Docker restart policy (FinishedAt→StartedAt gap < 5 s)
  - Compose deployment (config-hash label changed)
- Sends a detailed forensic alert including:
  - Exit code and OOMKilled flag captured **before** Docker clears them
  - Healthcheck probe history (last 5 probes with exit codes)
  - Host uptime, CPU%, and memory at time of alert
  - Last 100 log lines from the container
  - Recommended investigation steps
- Startup grace period prevents false alerts during container initialization

---

## 📝 Monitoring-Service Logs

Monitoring-Service writes its own action logs to the host:

```bash
/home/ubuntu/Monitoring-Service/monitoring-service.log
```

Included in this log:

- Startup / shutdown events
- Log directory watching status
- Detected log errors & warnings
- High CPU/Memory events and normalization
- Container health changes and restart detections (cause + forensics)
- Email alerts sent / suppressed / failures

🕒 All timestamps are logged in **IST (UTC+05:30)** for easier correlation with local events.

---

## 🧩 Technology Stack

| Component        | Purpose                               |
|------------------|---------------------------------------|
| Python           | Core implementation language          |
| watchdog         | Real-time log file monitoring         |
| psutil           | System resource (CPU/Mem) metrics     |
| docker SDK       | Container inspection (read-only)      |
| smtplib          | SMTP email alert delivery             |
| boto3 (AWS SES)  | SES email alert delivery (optional)   |
| requests         | Webhook alert delivery                |
| python-dotenv    | Configuration from `.env`             |
| Rotating logging | Stable monitoring-service audit logs  |
| Docker/Compose   | Deployment, isolation, reproducibility|

---

## ⚙️ Configuration via `.env`

All behavior is configured centrally through `.env`:

```env
# AWS SES email configuration
AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=YOUR_SECRET_ACCESS_KEY
AWS_REGION=ap-south-1
EMAIL_DEFAULT_SENDER=info@trustedweartech.com
EMAIL_DEFAULT_TO=deeepakumar@gmail.com,contact@trustedweartech.com

# Log error detection (serious warnings)
LOG_ERROR_KEYWORDS=Exception,Traceback,not authorized,Unauthorized,Could not check database,permission denied,failed to

# System resource monitoring
CPU_THRESHOLD_PERCENT=85
CPU_DURATION_SECONDS=60
MEM_THRESHOLD_PERCENT=85
MEM_DURATION_SECONDS=60
RESOURCE_CHECK_INTERVAL_SECONDS=5

# Docker health monitoring
DOCKER_CHECK_INTERVAL_SECONDS=15

# Alert cooldown (seconds)
ALERT_COOLDOWN_SECONDS=600

# Monitoring-Service log output (inside container)
MONITOR_LOG_DIR=/monitoring-service/logs
MONITOR_LOG_LEVEL=INFO

# Log directories (inside container)
LOG_DIRECTORIES=/logs/citra-service,/logs/citra-user-service,/logs/playwright-render-service
```

- No command-line flags are required at runtime.
- Adjust thresholds, keywords, or directories without changing code.

---

## 🚦 Deployment

From the Monitoring-Service project root:

```bash
docker compose build
docker compose up -d
```

To stop:

```bash
docker compose down
```

To restart the running container:

```bash
docker restart monitoring-service
```

---

## 🔍 Verifying Monitoring

### 1️⃣ View Monitoring-Service Internal Logs

```bash
tail -f /home/ubuntu/Monitoring-Service/monitoring-service.log
```

### 2️⃣ Test System Resource Alerts (CPU)

Temporarily lower thresholds in `.env` for testing if needed, then:

```bash
python3 -c "while True: pass" &
```

Expect:

- `[System Alert] High CPU/Memory usage` email
- Corresponding entries in `monitoring-service.log`

Stop the test:

```bash
pkill -f "while True: pass"
```

### 3️⃣ Test Docker Container Health Monitoring

Create an intentionally unhealthy container:

```bash
docker run -d       --name health-test       --health-cmd="exit 1"       --health-interval=5s       --health-retries=1       nginx:alpine
```

Expect:

- `[Docker Alert] Container unhealthy: health-test (...)` alert email with forensic details
- Corresponding entries in `monitoring-service.log`
- **Note:** Monitoring-Service will NOT restart the container. Use `docker restart health-test` to recover manually.

Cleanup:

```bash
docker rm -f health-test
```

### 📅 Daily Health Report Email (NEW)

Automated daily diagnostic report including:
- Current time (IST) & system uptime
- CPU, Memory & Disk usage
- List of Docker containers with status + health state
- Recent Monitoring-Service log activity (last N lines)

Trigger manually:

```bash
docker exec monitoring-service python -m app.daily_report
```

Recommended daily cron:

```bash
0 9 * * * docker exec monitoring-service python -m app.daily_report >> /home/ubuntu/Monitoring-Service/daily_report.log 2>&1
```

---

## 🧠 Alert Behavior Summary

| Event Type                          | Action                        | Alert Behavior                          |
|-------------------------------------|-------------------------------|-----------------------------------------|
| New log error / serious warning     | Logged + evaluated            | Email sent (if not cooled down)         |
| Same log error repeated             | Logged                        | Suppressed by per-pattern cooldown      |
| Log file truncated / rotated        | Pointer reset safely          | No replay or duplicate alerts           |
| High CPU/Memory sustained           | Logged                        | System alert email                      |
| Docker container becomes unhealthy  | Forensic alert sent       | Docker alert email (with cause + logs)  |

The system is tuned to be:
- **Sensitive to real issues**
- **Robust against noise and duplicates**

---

## 🔮 Possible Future Enhancements

- Slack / Teams notification integration
- Hostname & uptime details in alert emails (for multi-host setups)
- Prometheus/Grafana metrics integration
- Web UI or dashboard for historical alerts
- Persistent alert state across restarts (cooldown history)
- Optional JSON log output for SIEM/log aggregation tools

---

Maintained by **Trustedwear Tech**.

**Proactive monitoring. Reliable uptime. Less firefighting.**
