#!/usr/bin/env bash
# The chart's smoke test on a kind cluster (kindnet enforces NetworkPolicy) (.github/workflows/helm.yml). Expects the images
# six-sevens/backend:kind and six-sevens/kong:kind already loaded into kind. No Claude, no Google, no real data:
# the Pub/Sub emulator and a stub Supabase stand in, and the secrets are placeholders.
#
#   bash deploy/helm/smoke/smoke.sh
set -euo pipefail

CHART=deploy/helm/six-sevens
SMOKE=deploy/helm/smoke
WORKERS="doc-worker detection-worker case-worker audit-metrics"
pids=""
cleanup() { for p in $pids; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

step() { echo; echo "=== $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

step "Stubs: Pub/Sub emulator, Supabase, pulled subscriptions"
kubectl create namespace stubs
kubectl -n stubs create configmap smoke --from-file="$SMOKE/supabase_stub.py" --from-file="$SMOKE/publish.py"
kubectl apply -f "$SMOKE/stubs.yaml"
kubectl -n stubs rollout status deploy/pubsub-emulator --timeout=600s
kubectl -n stubs rollout status deploy/supabase-stub --timeout=300s
kubectl -n stubs wait --for=condition=complete job/pubsub-init --timeout=300s

step "Install the chart"
helm install six "$CHART" -f "$CHART/values-roles.yaml" -f "$CHART/values-kind.yaml"

step "Placeholder secrets (no real key anywhere; an invalid Anthropic key can't spend tokens)"
# A JWT-shaped placeholder: the Supabase client checks the key's shape.
jwt="eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c21va2U"
for ns in api $WORKERS gateway; do
  kubectl -n "$ns" create secret generic app-secrets \
    --from-literal=SUPABASE_PUBLISHABLE_KEY="$jwt" --from-literal=SUPABASE_SECRET_KEY="$jwt" \
    --from-literal=AUTH_USERS="" --from-literal=AUTH_SECRET_KEY=smoke-signing-key \
    --from-literal=GATEWAY_SECRET=smokegatewaykey0123456789 --from-literal=ANTHROPIC_API_KEY=smoke-no-calls \
    --from-literal=DATA_GOV_SG_API_KEY=" " --from-literal=URA_API_KEY=" "
done

step "Every pod becomes ready"
for name in api $WORKERS; do kubectl -n "$name" rollout status "deploy/$name" --timeout=600s; done
kubectl -n gateway rollout status deploy/kong --timeout=300s

forward() {  # forward <namespace> <target> <local:remote>
  kubectl -n "$1" port-forward "$2" "$3" >/dev/null 2>&1 &
  pids="$pids $!"
  sleep 3
}
expect() {  # expect <status> <method> <url> [curl args...]
  want=$1; method=$2; url=$3; shift 3
  got=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "$url" "$@")
  echo "$method $url -> $got (want $want)"
  [ "$got" = "$want" ] || fail "$method $url returned $got"
}

step "Must pass: through Kong to api"
forward gateway svc/kong 18000:80
expect 200 GET http://localhost:18000/health
curl -s http://localhost:18000/health | grep -q '"status":"ok"' || fail "Kong's /health isn't the app's reply"
expect 401 GET http://localhost:18000/cases
expect 401 GET http://localhost:18000/audit/events
expect 404 GET http://localhost:18000/metrics
expect 404 POST http://localhost:18000/push/doc-worker

step "Must refuse: api called around Kong (no gateway key)"
forward api svc/api 18001:80
expect 403 POST http://localhost:18001/auth/login -H 'Content-Type: application/json' -d '{"username":"x","password":"x"}'

step "Must block: the network policies"
# From outside the app's namespaces, nothing reaches api.
if kubectl run probe --rm -i --restart=Never --image=curlimages/curl:8.11.1 -n default --command -- \
    curl -s -m 5 -o /dev/null http://api.api.svc.cluster.local/health; then
  fail "a pod in default reached api"
fi
echo "default -> api: blocked"
# A worker can't call api either (workers talk only through events and the database).
if kubectl -n doc-worker exec deploy/doc-worker -- python -c \
    "import urllib.request; urllib.request.urlopen('http://api.api.svc.cluster.local/health', timeout=5)" 2>/dev/null; then
  fail "doc-worker reached api"
fi
echo "doc-worker -> api: blocked"

step "Must pass: one event reaches each worker by pull"
kubectl -n stubs run publish --rm -i --restart=Never --image=six-sevens/backend:kind --overrides='{"spec":{"containers":[{"name":"publish","image":"six-sevens/backend:kind","imagePullPolicy":"Never","command":["python","/smoke/publish.py"],"env":[{"name":"EVENT_TRANSPORT","value":"pubsub"},{"name":"GOOGLE_CLOUD_PROJECT","value":"local"},{"name":"PUBSUB_EMULATOR_HOST","value":"pubsub-emulator.stubs.svc.cluster.local:8085"}],"volumeMounts":[{"name":"smoke","mountPath":"/smoke"}]}],"volumes":[{"name":"smoke","configMap":{"name":"smoke"}}]}}'
# Read each worker's metrics from inside its pod: no port-forward to go wrong.
metrics() {
  kubectl -n "$1" exec "deploy/$1" -- python -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9100/metrics', timeout=5).read().decode())" \
    2>/dev/null || true
}
for role in $WORKERS; do
  for _ in $(seq 1 30); do
    if metrics "$role" | grep -q "^events_handled_total{role=\"$role\""; then
      echo "$role: handled its event"; continue 2
    fi
    sleep 4
  done
  echo "--- $role's log"; kubectl -n "$role" logs "deploy/$role" --tail=40 || true
  echo "--- $role's events_handled"; metrics "$role" | grep "^events_handled" || echo "(none)"
  echo "--- the emulator's log"; kubectl -n stubs logs deploy/pubsub-emulator --tail=40 || true
  fail "$role handled no event in 2 minutes"
done

echo; echo "Smoke test passed."
