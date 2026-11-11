#!/usr/bin/env bash
set -e

APP_DIR="/opt/disparador-fornecedores"

echo "=== 1. Instalando Python3, Node.js 20 e Git na VPS ==="
sudo apt-get update -y
sudo apt-get install -y curl git python3

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "=== 2. Instalando pacotes Node (Baileys / Express / QRCode) ==="
cd "$APP_DIR"
npm install --omit=dev

echo "=== 3. Configurando servico systemd 24/7 (disparador-pxt) ==="
cat <<EOF | sudo tee /etc/systemd/system/disparador-pxt.service
[Unit]
Description=Painel Python VPS - Disparador WhatsApp Fornecedores
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment=PORT=3050
Environment=BRIDGE_PORT=3051
ExecStart=/usr/bin/python3 -u $APP_DIR/painel.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable disparador-pxt
sudo systemctl restart disparador-pxt

# Liberar porta 3050 no UFW caso esteja ativo
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow 3050/tcp || true
fi

echo "=== CONCLUIDO! Painel online na porta 3050 (24/7) ==="
