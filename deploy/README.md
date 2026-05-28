# Deploying news-aggregator on a Linux VPS

Tested against Debian 12 / Ubuntu 22.04+. Requires Python 3.12+.

## 1. Install Python 3.12 (if not present)

Debian 12 ships with 3.11; install 3.12 from deadsnakes or compile. Adjust
`python3` to `python3.12` in the commands below if needed.

## 2. Create system user and directories

```bash
sudo useradd -r -s /usr/sbin/nologin news-bot
sudo mkdir -p /opt/news-aggregator /var/lib/news-aggregator
sudo chown -R news-bot:news-bot /opt/news-aggregator /var/lib/news-aggregator
```

## 3. Deploy code

```bash
sudo -u news-bot git clone <repo-url> /opt/news-aggregator
cd /opt/news-aggregator
sudo -u news-bot python3 -m venv .venv
sudo -u news-bot .venv/bin/pip install -e .
sudo -u news-bot python3 scripts/vendor_last30days.py
sudo -u news-bot cp config.example.toml config.toml
sudo -u news-bot cp .env.example .env
sudo -u news-bot $EDITOR /opt/news-aggregator/config.toml
sudo -u news-bot $EDITOR /opt/news-aggregator/.env
```

Restrict permissions on `.env` — it holds the Telegram bot token and OpenAI API key:

```bash
sudo chmod 600 /opt/news-aggregator/.env
sudo chown news-bot:news-bot /opt/news-aggregator/.env
```

## 4. Install systemd unit

```bash
sudo cp deploy/news-aggregator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now news-aggregator
sudo systemctl status news-aggregator
journalctl -u news-aggregator -f
```

## 5. Verify

Send `/status` to your Telegram bot. You should receive a status reply within a
second. Wait for the next cron tick (or run `sudo -u news-bot /opt/news-aggregator/.venv/bin/python -m aggregator --config /opt/news-aggregator/config.toml run --topic crypto_general`) to verify a digest arrives.

## Watchdog and auto-recovery

The unit runs with `Type=notify` + `WatchdogSec=180`. The bot pings systemd
from the asyncio event loop every 60s; if the loop wedges (the failure mode
from the 2026-05-28 incident, where one Telegram polling network error left
the process alive but mute for 17 hours), systemd sends SIGTERM/SIGKILL after
180s and `Restart=on-failure` brings it back. Confirm a restart was triggered
by watchdog (not by exit code) with:

```bash
journalctl -u news-aggregator -g "Watchdog timeout"
```

To tune the timeout, edit `WatchdogSec=` in the unit. Keep the in-process
interval (`_WATCHDOG_INTERVAL_S` in `aggregator/__main__.py`) at roughly half
of it so a single missed ping doesn't trigger a restart.

## Updating

```bash
cd /opt/news-aggregator
sudo -u news-bot git pull
sudo -u news-bot .venv/bin/pip install -e .
sudo systemctl restart news-aggregator
```

## Backing up

The only stateful file is `/var/lib/news-aggregator/aggregator.db`. Snapshot it with your usual backup tool.
