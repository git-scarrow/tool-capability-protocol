#!/bin/bash
echo "🎯 Completing TCP Deployment..."

ssh -i /Users/sam/.ssh/tcp_deployment_key root@167.99.149.241 << 'EOF'
set -e

echo "🔧 Completing TCP setup..."

# Extract application files
cd /opt/tcp-knowledge-system
tar -xzf /tmp/tcp-app.tar.gz -C scripts/
echo "✅ TCP application files extracted"

# Fix Python environment
cd /opt/tcp-knowledge-system
rm -rf tcp-env
python3 -m venv tcp-env
source tcp-env/bin/activate
pip install --upgrade pip
pip install anthropic schedule
echo "✅ Python environment ready"

# Get API key and create environment file
: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY before running deployment}"
cat > .env << ENV_EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
PYTHONPATH=/opt/tcp-knowledge-system/scripts
ENV_EOF
chmod 600 .env

# Create systemd service properly
cat > /etc/systemd/system/tcp-knowledge-growth.service << 'SERVICE_EOF'
[Unit]
Description=TCP Knowledge Growth System
After=network.target

[Service]
Type=simple
User=tcp
Group=tcp
WorkingDirectory=/opt/tcp-knowledge-system
Environment=PYTHONPATH=/opt/tcp-knowledge-system/scripts
EnvironmentFile=/opt/tcp-knowledge-system/.env
ExecStart=/opt/tcp-knowledge-system/tcp-env/bin/python scripts/simple_tcp_deployment.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# Create tcp user and set permissions
useradd -m -s /bin/bash tcp || true
chown -R tcp:tcp /opt/tcp-knowledge-system

# Reload and start service
systemctl daemon-reload
systemctl enable tcp-knowledge-growth
systemctl start tcp-knowledge-growth

echo "✅ TCP Knowledge Growth system started!"

sleep 3
systemctl status tcp-knowledge-growth --no-pager

echo ""
echo "📊 Checking for activity..."
ls -la /opt/tcp-knowledge-system/
EOF
