FROM node:22-alpine AS build

WORKDIR /app

# Vite inlines import.meta.env.VITE_* at build time, so the backend's URL
# has to be known before this stage runs — the deploy pipeline passes it in
# once the backend Cloud Run service exists (see .github/workflows/deploy.yml).
# Defaults to localhost so a plain `docker compose build` (no --build-arg)
# still matches local-dev behavior instead of baking in an empty string.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

# Hides internal dev-only tools (e.g. the spelling-anomaly test link) from
# the sponsor-facing cloud build — set true only in deploy.yml's frontend
# build step. Defaults false so local `npm run dev` and a plain
# `docker compose build` still show them.
ARG VITE_HIDE_DEV_FEATURES=false
ENV VITE_HIDE_DEV_FEATURES=$VITE_HIDE_DEV_FEATURES

COPY frontend/package.json ./package.json
COPY frontend/tsconfig.json ./tsconfig.json
COPY frontend/vite.config.ts ./vite.config.ts
COPY frontend/eslint.config.js ./eslint.config.js
COPY frontend/index.html ./index.html
COPY frontend/src ./src

RUN npm install
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
