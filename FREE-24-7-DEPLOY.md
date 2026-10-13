# Deploy Y.T.B MARKET BOT on a free always-on VM

## Important reality about “free 24/7”

Free hosting platforms often sleep or restart workloads. Render's official free plan spins a service down after 15 minutes without inbound traffic and says free instances are not for production applications [1]. Cloudflare Workers can host Discord interaction/webhook apps, but that is a different architecture from this Python gateway bot, which maintains a Discord Gateway connection [2].

The closest free option for this bot is an **Oracle Cloud Always Free Ubuntu VM**. Oracle documents Always Free compute resources for the life of an account, including an AMD micro VM or Ampere A1 resources [3]. Oracle can reclaim an Always Free VM that stays below its idle thresholds for seven days, so no provider can honestly promise zero interruptions forever. This bot's Discord Gateway connection and 15-minute roster sync provide normal activity, but keep backups and check logs.

## 1. Create the free VM

1. Open the [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/).
2. Create an account and choose your home region carefully. Always Free compute must be created in the home region.
3. Create a Compute Instance:
   - Image: **Ubuntu**
   - Shape: **VM.Standard.E2.1.Micro** (AMD, 1 GB RAM), or **VM.Standard.A1.Flex** with 1 OCPU and 2 GB RAM if available.
   - Select an **Always Free-eligible** shape.
   - Assign a public IPv4 address.
   - Save the SSH private key when Oracle offers it.
4. In the VM's networking rules, allow **TCP 22** for SSH. The bot does not need an inbound web port because it sends HTTPS requests to the Proof Desk website.

Oracle may ask for payment-card verification during signup even when you stay inside Always Free limits.

## 2. Connect by SSH

From your computer, replace the placeholders with your real key and VM address:

```bash
chmod 600 oracle.key
ssh -i oracle.key ubuntu@YOUR_VM_PUBLIC_IP
```

## 3. Install Python and prepare the bot

Run on the Oracle VM:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
sudo mkdir -p /opt/ytb-ticket-bot
sudo chown -R "$USER":"$USER" /opt/ytb-ticket-bot
```

Copy the bot project to the VM. From the machine that contains this project, run:

```bash
scp -i oracle.key -r /home/ubuntu/discord_ticket_bot/* ubuntu@YOUR_VM_PUBLIC_IP:/opt/ytb-ticket-bot/
```

Copy the environment file separately and never commit it to GitHub:

```bash
scp -i oracle.key /home/ubuntu/discord_ticket_bot/.env ubuntu@YOUR_VM_PUBLIC_IP:/opt/ytb-ticket-bot/.env
```

Back on the Oracle VM:

```bash
cd /opt/ytb-ticket-bot
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
chmod 600 .env
python -m py_compile bot.py
```

Your `.env` must contain the existing bot settings, including:

```text
DISCORD_BOT_TOKEN=your_token
WEBSITE_INGESTION_ENDPOINT=https://ytbdesk-3jcys2g3.manus.space/api/ingest
INGESTION_SECRET=the_same_secret_used_by_the_website
```

Do not paste the token or secret into public chats or a public repository. Because the bot token was previously shared in chat, regenerate it in the Discord Developer Portal before using the VM, then update `.env` with the new token.

## 4. Run it as an automatic system service

Create the service:

```bash
sudo tee /etc/systemd/system/ytb-ticket-bot.service >/dev/null <<'UNIT'
[Unit]
Description=Y.T.B MARKET BOT
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/ytb-ticket-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/ytb-ticket-bot/.venv/bin/python /opt/ytb-ticket-bot/bot.py
Restart=always
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ytb-ticket-bot
sudo systemctl status ytb-ticket-bot --no-pager
```

The important setting is `Restart=always`. If Python exits, the VM reboots, or Discord disconnects, systemd starts the bot again.

## 5. Check logs

Live logs:

```bash
sudo journalctl -u ytb-ticket-bot -f
```

Recent logs:

```bash
sudo journalctl -u ytb-ticket-bot --since "1 hour ago" --no-pager
```

You should see messages such as:

```text
Logged in as Y.T.B MARKET BOT
Member roster sync ... uploaded
```

Check service health:

```bash
systemctl is-active ytb-ticket-bot
systemctl is-enabled ytb-ticket-bot
```

Both should print `active` and `enabled`.

## 6. Updating the bot later

Copy changed files to the VM, then restart safely:

```bash
sudo systemctl stop ytb-ticket-bot
# copy the updated bot.py and any changed project files here
sudo systemctl start ytb-ticket-bot
sudo journalctl -u ytb-ticket-bot -n 50 --no-pager
```

Do not overwrite `.env` during code updates.

## What this gives you

- Runs independently of the Manus Sandbox session
- Starts automatically when the VM boots
- Restarts automatically after a bot crash
- Keeps the Discord Gateway connection active
- Continues member, staff, blacklist, role, banner, avatar, and ticket-proof synchronization
- Does not require Outlook, Gmail, Apple Mail, or Discord to remain open on your own computer

### References

[1]: https://render.com/docs/free "Render free instance limitations"
[2]: https://docs.discord.com/developers/tutorials/hosting-on-cloudflare-workers "Discord official Cloudflare Workers tutorial"
[3]: https://docs.oracle.com/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm "Oracle Cloud Always Free resources"

## Optional UptimeRobot monitoring

The bot now exposes a small HTTP endpoint at `/health`. It returns a success response and reports whether the Discord client is ready. The port comes from `PORT` and defaults to `8080`.

On a real host with a public URL:

1. Open [UptimeRobot](https://uptimerobot.com/) and create an account.
2. Choose **Add New Monitor**.
3. Monitor type: **HTTP(s)**.
4. URL: `https://YOUR_BOT_HOST/health`.
5. Monitoring interval: **5 minutes**.
6. Add an alert email or notification channel.
7. Save the monitor.

UptimeRobot is useful for alerts and for waking a host that intentionally sleeps. It is not a replacement for `systemd`, and it cannot prevent the Manus Sandbox from hibernating. On Oracle, use `systemd` as the uptime mechanism and use UptimeRobot only to notify you if `/health` stops responding.

For the current temporary Sandbox process, the test URL is:

```text
https://8080-i5x54jpkida5gdzbjf48n-e55bfa7b.sg2.manus.computer/health
```

This URL is temporary and should not be used as a permanent production monitor. Replace it with the public URL of your Oracle VM or other persistent host after deployment.
