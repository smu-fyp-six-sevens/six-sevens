FROM node:22-alpine AS build

WORKDIR /app

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
EXPOSE 80
