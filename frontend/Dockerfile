FROM node:22.23.3-alpine AS build

WORKDIR /app

# Dependencies first, so a source change doesn't reinstall them.
# Exactly the lock file's versions; fails if package.json and the lock disagree.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Vite inlines import.meta.env.VITE_* at build time, so the backend's URL
# has to be known before this stage runs — the deploy pipeline passes it in
# once the backend Cloud Run service exists (see .github/workflows/deploy.yml).
# Defaults to localhost so a plain `docker compose build` (no --build-arg)
# still matches local-dev behavior instead of baking in an empty string.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

COPY frontend/tsconfig.json ./tsconfig.json
COPY frontend/vite.config.ts ./vite.config.ts
COPY frontend/eslint.config.js ./eslint.config.js
COPY frontend/index.html ./index.html
COPY frontend/src ./src
RUN npm run build

FROM nginx:1.30.6-alpine
# nginx's workers, which serve requests, already run as the nginx user; only the master is root, to bind port 80.
# Going fully non-root means nginx-unprivileged on 8080 and a Terraform port change (PENDING_WORK.md).
#checkov:skip=CKV_DOCKER_3:Workers run as nginx; the root master only binds port 80 (see above).

COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
# The CSP may call only the API the bundle was built for (and its WebSocket): fill its origin in.
ARG VITE_API_URL=http://localhost:8000
RUN API_WS_URL=$(printf '%s' "$VITE_API_URL" | sed 's|^http|ws|') \
    && sed -i -e "s|__API_ORIGIN__|$VITE_API_URL|g" -e "s|__API_WS_ORIGIN__|$API_WS_URL|g" /etc/nginx/conf.d/default.conf \
    && nginx -t
EXPOSE 80
# Cloud Run ignores this and uses its own probes; compose and docker ps show it.
HEALTHCHECK --interval=30s --timeout=5s CMD ["curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1/"]
