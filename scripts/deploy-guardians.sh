#!/usr/bin/env bash
# Execute na VPS com o diretório de um git archive já publicado no GitHub.
set -euo pipefail
release="$(cd "$1" && pwd)"
app=/opt/disparador-fornecedores
backup="/opt/backups/ofornecedor-guardians-$(date -u +%Y%m%dT%H%M%SZ)"
files=(painel.py daily_review.py parser_listas.py price_guardian.py whatsapp_bridge.js AGENTS.md tests/test_supplier_corrections.py)
cd "$release"
TZ=America/Sao_Paulo python3 -m unittest discover tests
node --test tests/catalog.test.cjs
node --check whatsapp_bridge.js
mkdir -p "$backup"
chmod 700 "$backup"
# Backup apenas do código existente; contas/runtime nunca são substituídos.
for file in "${files[@]}"; do
  if [[ -f "$app/$file" ]]; then
    mkdir -p "$backup/$(dirname "$file")"
    cp -p "$app/$file" "$backup/$file"
  fi
done
restore() {
  for file in "${files[@]}"; do
    if [[ -f "$backup/$file" ]]; then cp -p "$backup/$file" "$app/$file"; fi
  done
  systemctl restart disparador-pxt.service
}
trap restore ERR
systemctl stop disparador-pxt.service
for file in "${files[@]}"; do cp "$release/$file" "$app/$file"; done
systemctl start disparador-pxt.service
healthy=0
for attempt in {1..30}; do
  if curl -fsS --max-time 2 http://127.0.0.1:3050/api/ping >/dev/null; then healthy=1; break; fi
  sleep 1
done
test "$healthy" = 1
systemctl is-active --quiet disparador-pxt.service
trap - ERR
printf 'DEPLOY_OK backup=%s\n' "$backup"
