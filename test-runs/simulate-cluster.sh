#!/usr/bin/env bash
# Simulates Couchbase cluster events on the local docker setup, so data_no_syncing's cluster check can be seen firing.
#   add        start a second node (cb-node2) and add it to the cluster (pending until a rebalance)
#   rebalance  rebalance it in, watching the check while it runs
#   pause      freeze cb-node2: about a minute later it turns unhealthy;  unpause brings it back
#   remove     rebalance cb-node2 out again, watching the check
#   delete     remove the cb-node2 container (after remove)
#   watch [s]  print the cluster check once a second for s seconds (default 30)
#
# Adding a node renames cb-local from 127.0.0.1 to its docker IP for good (Couchbase won't rename a node that has
# buckets). If docker later gives cb-local another IP, Couchbase won't start ("Configured address ... seems to be
# invalid"). Host clients also need an alternate address, since the docker IP isn't reachable from the Mac:
#   couchbase-cli setting-alternate-address --set --node <docker IP> --hostname localhost
set -euo pipefail
cd "$(dirname "$0")/.."

NODE=cb-node2
NETWORK=local-docker_default
IMAGE=couchbase/server:community-7.6.2

read -r CB_USER CB_PASS < <(.venv/bin/python -c 'from src.utils.get_env import get_env_value as g; print(g("CB_USERNAME"), g("CB_PASSWORD"))')
node_ip() { docker inspect "$NODE" -f "{{(index .NetworkSettings.Networks \"$NETWORK\").IPAddress}}"; }
cli() { docker exec cb-local /opt/couchbase/bin/couchbase-cli "$@" -c localhost -u "$CB_USER" -p "$CB_PASS"; }
# couchbase-cli rebalance can't find the HTTPS port in this container, so rebalance over REST; $1 is the node to eject
rebalance() {
  local known
  known=$(curl -sf -u "$CB_USER:$CB_PASS" http://localhost:8091/pools/default \
    | .venv/bin/python -c 'import json, sys; print(",".join(n["otpNode"] for n in json.load(sys.stdin)["nodes"]))')
  curl -sf -u "$CB_USER:$CB_PASS" -X POST http://localhost:8091/controller/rebalance -d "knownNodes=$known" -d "ejectedNodes=${1:-}"
}

watch_check() {
  .venv/bin/python - "${1:-30}" <<'EOF'
import asyncio, sys, time
from src.integrations.cb_admin import cluster_problems

async def main(seconds):
    for _ in range(seconds):
        problems = await cluster_problems()
        print(time.strftime("%H:%M:%S"), "; ".join(problems) or "healthy", flush=True)
        await asyncio.sleep(1)

asyncio.run(main(int(sys.argv[1])))
EOF
}

case "${1:-}" in
  add)
    docker run -d --name "$NODE" --network "$NETWORK" "$IMAGE" >/dev/null
    until docker exec "$NODE" curl -sf -o /dev/null http://localhost:8091/ui/index.html; do sleep 2; done
    # Couchbase rejects short hostnames, so the node joins by its IP
    cli server-add --server-add "$(node_ip)" --server-add-username "$CB_USER" --server-add-password "$CB_PASS" --services data
    ;;
  rebalance) rebalance; watch_check 30 ;;
  pause) docker pause "$NODE"; watch_check 90 ;;  # Couchbase takes about a minute to call it unhealthy
  unpause) docker unpause "$NODE"; watch_check 20 ;;
  remove) rebalance "ns_1@$(node_ip)"; watch_check 30 ;;
  delete) docker rm -f "$NODE" ;;
  watch) watch_check "${2:-30}" ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
