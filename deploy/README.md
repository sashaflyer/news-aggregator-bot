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

## Updating

```bash
cd /opt/news-aggregator
sudo -u news-bot git pull
sudo -u news-bot .venv/bin/pip install -e .
sudo systemctl restart news-aggregator
```

## Backing up

The only stateful file is `/var/lib/news-aggregator/aggregator.db`. Snapshot it with your usual backup tool.
