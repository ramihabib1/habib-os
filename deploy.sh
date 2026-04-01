#!/usr/bin/env bash
# Deploy Habib OS to Hetzner VPS
# Usage: ./deploy.sh
# Requires: SSH key configured for habib@204.168.188.203

set -euo pipefail

VPS_HOST="habib@204.168.188.203"
REMOTE_DIR="/home/habib/habib-os"

echo "=== Deploying Habib OS ==="

# Push latest code (assumes git remote is set up)
echo "→ Pulling latest code on VPS..."
ssh "$VPS_HOST" "cd $REMOTE_DIR && git pull origin main"

# Install/update dependencies
echo "→ Installing dependencies..."
ssh "$VPS_HOST" "cd $REMOTE_DIR && .venv/bin/pip install -r requirements.txt --quiet"

# Restart PM2 processes
echo "→ Restarting PM2 processes..."
ssh "$VPS_HOST" "cd $REMOTE_DIR && pm2 restart ecosystem.config.js --update-env"

# Show status
echo "→ PM2 status:"
ssh "$VPS_HOST" "pm2 status"

echo "=== Deploy complete ==="
