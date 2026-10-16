#!/command/with-contenv bashio
# Entry point inside the Home Assistant app container.
# Options are read from /data/options.json (written by the Supervisor) with jq.
set -e

remote="$(jq -r '.snapshot_remote // ""' /data/options.json 2>/dev/null || true)"
if [ -n "${remote}" ] && [ ! -f /data/deploy_key ]; then
  ssh-keygen -q -t ed25519 -N "" -C "shelly-manager@home-assistant" -f /data/deploy_key
  chmod 600 /data/deploy_key
  bashio::log.info "Generated a deploy key for the config backup repository."
fi
if [ -f /data/deploy_key.pub ]; then
  bashio::log.info "Add this public key as a deploy key WITH write access on the backup repository:"
  bashio::log.info "$(cat /data/deploy_key.pub)"
fi

export SM_MODE=ha
exec /app/.venv/bin/shelly-manager serve
