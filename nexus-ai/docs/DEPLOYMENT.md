# Deployment runbook (Ubuntu 24.04 VPS, Docker Compose)

Target: 4 vCPU, 8 GB RAM, 100 GB SSD. Measured footprint of the full production stack
(11 containers, paper mode): **≈ 0.9 GB RAM**, idle CPU near zero, ≈ 1.2 GB of images.

> Location matters: `api.binance.com` returned **HTTP 451** from the build environment.
> Public market data works via Binance's official mirrors (configured by default), but
> **trading** on Binance requires a VPS in a jurisdiction Binance serves, and an account
> eligible there. Check Binance/Kraken/Coinbase terms for your country before choosing
> the VPS region. Nothing here bypasses geographic restrictions.

## 1. Provision (once, as root)

```bash
git clone <repo> /opt/nexus-src && cd /opt/nexus-src/nexus-ai
sudo bash scripts/provision-ubuntu.sh deploy
```
Installs Docker from Docker's repository, creates the `deploy` user, enables ufw
(22/80/443 only), key-only SSH (if keys are present), fail2ban, unattended security
upgrades, UTC + NTP.

## 2. Configure (as `deploy`)

```bash
cd /opt/nexus/nexus-ai            # clone here as the deploy user
cp .env.example .env && chmod 600 .env
scripts/generate-secrets.sh        # paste into .env
mkdir -p /opt/nexus/secrets && openssl rand -hex 32 > /opt/nexus/secrets/backup_passphrase
chmod 600 /opt/nexus/secrets/backup_passphrase   # COPY IT OFF THE SERVER (password manager)
```
Set in `.env`: `NEXUS_APP_ENV=production`, `NEXUS_DOMAIN`, `BACKUP_PASSPHRASE_FILE`,
optionally `NEXUS_TELEGRAM_BOT_TOKEN` / `NEXUS_TELEGRAM_CHAT_ID`. Leave
`NEXUS_TRADING_MODE=paper`.

## 3. First start

```bash
scripts/deploy.sh                                   # build, migrate, start
scripts/init-letsencrypt.sh you@example.com         # real TLS certificate
docker compose -f docker-compose.yml -f infrastructure/docker/compose.prod.yml \
  exec api python -m nexus_api.cli create-owner --email you@example.com
```
Then sign in at `https://<domain>/`, open **Security → Set up 2FA**.

Research data and validation (run whenever you want fresh evaluations):
```bash
C="docker compose -f docker-compose.yml -f infrastructure/docker/compose.prod.yml"
$C run --rm api python -m nexus_market.cli backfill --exchange binance --days 730
$C run --rm api python -m nexus_backtest.cli validate-strategies
$C run --rm api python -m nexus_ai.cli train
```
Strategies/models are enabled only if they pass out-of-sample gates.

## 4. Operations

| Task | Command |
|---|---|
| Update | `git pull && scripts/deploy.sh` (takes a pre-deploy backup) |
| Status | `$C ps` · dashboard **System** page · Telegram alerts from `monitor` |
| Logs | `$C logs -f --tail=200 trader` (JSON, secrets redacted, rotated 5×20 MB) |
| Emergency stop | dashboard **Risk → Engage kill switch** (no 2FA needed to stop) |
| Backups | daily 02:xx UTC, AES-256 (gpg), 14-day retention, volume `backups` |
| Off-site copy | copy `/var/lib/docker/volumes/*_backups/_data/*.gpg` off the server (e.g. rclone to object storage); encrypted at rest |
| Restore drill | `scripts/restore-backup.sh <file> nexus_restore_check` (never touches live DB; compares row counts) |
| Staging | `docker compose -p nexus-staging -f docker-compose.yml -f infrastructure/docker/compose.staging.yml --env-file .env.staging up -d` (paper only, own volumes, ports 8200/5533 on localhost) |

Automatic restarts: every service has `restart: unless-stopped` plus a healthcheck;
verified by killing the API process (restarted) during testing.

## 5. Verified locally (2026-11-14)

Full production overlay with a self-signed certificate: all 11 services healthy;
HTTP→HTTPS redirect; HTTP/2 + TLS; HSTS/CSP/frame headers; `/health/ready` blocked
externally (403); unknown paths 404; anonymous API 401; login through Nginx with a
`Secure` cookie; login rate limit → 429; services connected as `nexus_app`; encrypted
backup → checksum → decrypt → restore into a new database with **all 23 tables matching**;
25-minute continuous run with 0 restarts/errors; Nginx keeps routing after the API
container is recreated with a new IP (Docker DNS re-resolution).

Not verified (needs your VPS/domain): Let's Encrypt issuance, ufw/fail2ban on a real
host, off-site backup copies.

## 6. Live trading (later, only by the owner)

See docs/SECURITY.md. Requires validated strategies, API keys with withdrawals disabled,
2FA, explicit approval in the dashboard, and the configuration gates in `.env`.
