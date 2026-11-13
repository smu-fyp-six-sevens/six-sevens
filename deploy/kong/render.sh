#!/bin/sh
# Renders kong.yml from the template and environment, then starts Kong on $PORT (Cloud Run sets it).
set -eu

: "${API_URL:?API_URL is required}"
: "${AUDIT_URL:?AUDIT_URL is required}"
: "${CORS_ORIGINS:?CORS_ORIGINS is required (comma-separated frontend origins)}"
PRESERVE_HOST="${PRESERVE_HOST:-true}"
ORIGINS=$(printf '%s' "$CORS_ORIGINS" | sed 's/[[:space:]]//g; s/,/", "/g; s/^/"/; s/$/"/')

sed -e "s|\${API_URL}|$API_URL|g" \
    -e "s|\${AUDIT_URL}|$AUDIT_URL|g" \
    -e "s|\${PRESERVE_HOST}|$PRESERVE_HOST|g" \
    -e "s|\${CORS_ORIGINS}|$ORIGINS|g" \
    /kong/kong.yml.tmpl > /tmp/kong.yml

export KONG_DATABASE=off
export KONG_DECLARATIVE_CONFIG=/tmp/kong.yml
export KONG_PROXY_LISTEN="0.0.0.0:${PORT:-8000}"
export KONG_ADMIN_LISTEN=off
# /metrics for Prometheus, kept off the public port.
export KONG_STATUS_LISTEN="0.0.0.0:${KONG_STATUS_PORT:-8100}"
export KONG_PROXY_ACCESS_LOG=/dev/stdout
export KONG_PROXY_ERROR_LOG=/dev/stderr
# Rate limits key on the client's address. On Cloud Run every connection comes from Google's front end, which
# appends the real client to X-Forwarded-For: trust the peer to set it and take the last entry (recursive off),
# since anything to its left was sent by the client and could be spoofed.
export KONG_TRUSTED_IPS="${KONG_TRUSTED_IPS:-0.0.0.0/0,::/0}"
export KONG_REAL_IP_HEADER="${KONG_REAL_IP_HEADER:-X-Forwarded-For}"
export KONG_REAL_IP_RECURSIVE="${KONG_REAL_IP_RECURSIVE:-off}"

exec /docker-entrypoint.sh kong docker-start
