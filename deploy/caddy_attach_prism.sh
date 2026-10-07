#!/usr/bin/env bash
# Attach the PRISM site to Beam's running Caddy without changing Beam's own configuration.
#
# Beam's Caddyfile is a read-only bind mount, so PRISM is loaded as:
#   <the exact Caddyfile Beam's container is running> + <PRISM site block>
# written to Caddy's writable /config volume. Runs idempotently from a systemd timer:
# - if Beam's file already contains the PRISM block, nothing is done;
# - if the PRISM site is already live, nothing is done;
# - otherwise the combined file is rebuilt from Beam's *current* file, validated, and loaded.
# Beam's sites are always taken verbatim from its own file, so this never reverts Beam changes.
set -euo pipefail

CONTAINER="${CADDY_CONTAINER:-beam-caddy-1}"
HOST="app.getprismpulse.xyz"
BLOCK="$(cat <<EOF

# PRISM (read-only risk workbench), attached by caddy_attach_prism.sh. Beam's sites above are unchanged.
${HOST} {
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
	}
	reverse_proxy 172.18.0.1:8790
}
EOF
)"

docker inspect "$CONTAINER" >/dev/null 2>&1 || { echo "caddy container $CONTAINER not running"; exit 0; }
current="$(docker exec "$CONTAINER" cat /etc/caddy/Caddyfile)"
if grep -q "$HOST" <<<"$current"; then echo "Beam's Caddyfile already includes $HOST"; exit 0; fi
if docker exec "$CONTAINER" wget -qO- http://localhost:2019/config/ 2>/dev/null | grep -q "$HOST"; then echo "$HOST already live"; exit 0; fi

printf '%s\n%s\n' "$current" "$BLOCK" | docker exec -i "$CONTAINER" sh -c 'cat > /config/Caddyfile.with-prism'
docker exec "$CONTAINER" caddy validate --config /config/Caddyfile.with-prism --adapter caddyfile >/dev/null
docker exec "$CONTAINER" caddy reload --config /config/Caddyfile.with-prism --adapter caddyfile
echo "attached $HOST"
