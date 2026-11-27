#!/usr/bin/env bash
set -euo pipefail
# Run on the VPS with the unpacked release directory as the only argument.
release="$(cd "$1" && pwd)"
app=/opt/disparador-fornecedores
backup="/opt/backups/buscabusca-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
chmod 700 /opt/backups "$backup"
tar --exclude=node_modules --exclude=.git --exclude=__pycache__ -czf "$backup/application.tar.gz" -C "$app" .
chmod 600 "$backup/application.tar.gz"
if [ -d /var/lib/ofornecedor-db ]; then
  tar -czf "$backup/accounts-db.tar.gz" -C /var/lib/ofornecedor-db .
  chmod 600 "$backup/accounts-db.tar.gz"
fi
systemctl cat disparador-pxt > "$backup/service.txt"
cd "$release"
npm ci --omit=dev --no-audit --no-fund
npm test
python3 -m py_compile painel.py parser_listas.py price_guardian.py
node --check whatsapp_bridge.js
restore() {
  systemctl stop disparador-pxt || true
  tar -xzf "$backup/application.tar.gz" -C "$app"
  if test -d "$backup/node_modules"; then
    rm -rf "$app/node_modules"
    mv "$backup/node_modules" "$app/node_modules"
  fi
  systemctl start disparador-pxt
}
trap 'restore' ERR
systemctl stop disparador-pxt
mv "$app/node_modules" "$backup/node_modules"
mv "$release/node_modules" "$app/node_modules"
mkdir -p "$app/web" "$app/tests" "$app/scripts"
for file in painel.py daily_review.py color_guardian.py coverage_guardian.py access_control.py whatsapp_bridge.js capture_queue.js invite_queue.js invite_policy.js parser_listas.py price_guardian.py apple_reference.py apple_reference_seed.json package.json package-lock.json README.md .gitignore INICIAR_DISPARADOR.bat; do
  cp "$release/$file" "$app/$file"
done
cp "$release/web/"*.html "$release/web/"*.css "$release/web/"*.js "$app/web/"
cp "$release/tests/"*.py "$release/tests/"*.cjs "$app/tests/"
cp "$release/scripts/deploy-vps.sh" "$app/scripts/deploy-vps.sh"
cp "$release/scripts/review_catalog.py" "$app/scripts/review_catalog.py"
cp "$release/scripts/audit_display.mjs" "$app/scripts/"
cp "$release/scripts/patch-baileys.cjs" "$app/scripts/patch-baileys.cjs"
for file in audit_prods.cjs clean_groups.cjs purge_unwanted.cjs price_guardian.cjs disparador.py delivery_tracking.js; do
  rm -f "$app/$file"
done
rm -f "$app/tests/test_campaign.py" "$app/tests/delivery_tracking.test.cjs"
systemctl start disparador-pxt
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3050/api/products > "$backup/health.json"; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:3050/login | python3 -c 'import sys; assert "O Fornecedor" in sys.stdin.read()'
systemctl is-active --quiet disparador-pxt
trap - ERR
printf 'DEPLOY_OK backup=%s\n' "$backup"
