#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/buscabusca.srv2058756.hstgr.cloud" ]; then
    /usr/sbin/nginx -t
    /bin/systemctl reload nginx
fi
