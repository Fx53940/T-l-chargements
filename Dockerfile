FROM node:22-bookworm-slim AS build
WORKDIR /app
# outils de compilation requis par better-sqlite3 (node-gyp)
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 make g++ \
  && rm -rf /var/lib/apt/lists/*
COPY package.json ./
RUN npm install --omit=dev

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY package.json ./
COPY server ./server
COPY public ./public

RUN useradd --system --create-home appuser \
  && mkdir -p /app/data \
  && chown -R appuser:appuser /app
USER appuser

EXPOSE 3000
CMD ["node", "server/index.js"]
