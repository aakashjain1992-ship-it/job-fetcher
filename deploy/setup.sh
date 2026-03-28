#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Linode Nanode ($5/mo) — One-shot setup script
#
# Usage (run as root after SSH into fresh Ubuntu 22.04):
#   curl -fsSL https://raw.githubusercontent.com/YOUR_REPO/main/deploy/setup.sh | bash
#
# Or upload and run:
#   scp deploy/setup.sh root@YOUR_LINODE_IP:/root/
#   ssh root@YOUR_LINODE_IP bash /root/setup.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

APP_USER="jobfetcher"
APP_DIR="/opt/job-fetcher"
PYTHON_VERSION="python3.11"

echo "==> Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

echo "==> Installing dependencies..."
apt-get install -y -qq \
  python3.11 python3.11-venv python3-pip \
  nginx git curl ufw

echo "==> Creating app user..."
id -u $APP_USER &>/dev/null || useradd -m -s /bin/bash $APP_USER

echo "==> Creating app directory..."
mkdir -p $APP_DIR
chown $APP_USER:$APP_USER $APP_DIR

echo ""
echo "─────────────────────────────────────────────────────────"
echo "  MANUAL STEP: Upload your code to the server."
echo ""
echo "  From your local machine, run:"
echo "    rsync -av --exclude=venv --exclude=.env --exclude='*.db' \\"
echo "      /Users/aajai/Documents/job-fetcher/ root@YOUR_IP:$APP_DIR/"
echo ""
echo "  Then upload your .env:"
echo "    scp /Users/aajai/Documents/job-fetcher/.env root@YOUR_IP:$APP_DIR/.env"
echo "─────────────────────────────────────────────────────────"
echo ""
read -p "Press Enter once you've uploaded the code..."

echo "==> Setting up Python virtualenv..."
cd $APP_DIR
sudo -u $APP_USER $PYTHON_VERSION -m venv venv
sudo -u $APP_USER venv/bin/pip install -r requirements.txt -q

echo "==> Installing systemd service..."
cp deploy/jobfetcher.service /etc/systemd/system/jobfetcher.service
systemctl daemon-reload
systemctl enable jobfetcher
systemctl start jobfetcher

echo "==> Configuring nginx..."
cp deploy/nginx.conf /etc/nginx/sites-available/jobfetcher
ln -sf /etc/nginx/sites-available/jobfetcher /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "==> Configuring firewall..."
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> Installing weekly pipeline cron..."
cp deploy/pipeline-cron.sh /opt/pipeline-cron.sh
chmod +x /opt/pipeline-cron.sh
chown $APP_USER:$APP_USER /opt/pipeline-cron.sh
# Every Saturday at 6am UTC (11:30am IST)
(crontab -u $APP_USER -l 2>/dev/null; echo "0 6 * * 6 /opt/pipeline-cron.sh >> /var/log/job-fetcher-cron.log 2>&1") | crontab -u $APP_USER -

echo ""
echo "✅ Setup complete!"
echo ""
echo "   Dashboard: http://$(curl -s ifconfig.me)"
echo "   Service:   systemctl status jobfetcher"
echo "   Logs:      journalctl -u jobfetcher -f"
echo "   Pipeline:  sudo -u $APP_USER /opt/pipeline-cron.sh"
echo ""
