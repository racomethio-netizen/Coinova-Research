# syntax=docker/dockerfile:1.7
# Build context: the tamagne/ directory. Behind a TLS-intercepting proxy, add
#   --secret id=npm_ca,src=/path/to/proxy-ca.pem  (never baked into the image)
# Build context: the tamagne/ directory.  docker build -f apps/api/Dockerfile -t tamagne-api .
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d
# The Prisma schema engine needs OpenSSL, which the slim image lacks; the migrate job uses the full image.
ARG NODE_FULL_IMAGE=node:22-bookworm@sha256:7702d8aa4d5910871692af4480eb3ba86f23047a6417a44d857d06c95d060d3f

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json .npmrc tsconfig.base.json ./
COPY packages/core/package.json packages/core/
COPY packages/db/package.json packages/db/
COPY apps/api/package.json apps/api/
COPY apps/web/package.json apps/web/
RUN --mount=type=secret,id=npm_ca,required=false \
    sh -c '[ -f /run/secrets/npm_ca ] && export NPM_CONFIG_CAFILE=/run/secrets/npm_ca; npm ci --workspace @tamagne/core --workspace @tamagne/db --workspace @tamagne/api --include-workspace-root'
COPY packages packages
COPY apps/api apps/api
RUN npm run build -w @tamagne/core -w @tamagne/db -w @tamagne/api \
 && npm prune --omit=dev --workspace @tamagne/core --workspace @tamagne/db --workspace @tamagne/api --include-workspace-root

# One-off migration job: `docker compose run --rm migrate`. Contains the Prisma CLI; the API image does not.
FROM ${NODE_FULL_IMAGE} AS migrate
WORKDIR /app/packages/db
COPY --from=build /app/package.json /app/package-lock.json /app/.npmrc /app/
COPY packages/db/package.json ./
RUN --mount=type=secret,id=npm_ca,required=false \
    sh -c 'cd /app && [ -f /run/secrets/npm_ca ] && export NPM_CONFIG_CAFILE=/run/secrets/npm_ca NODE_EXTRA_CA_CERTS=/run/secrets/npm_ca; npm ci --workspace @tamagne/db --include-workspace-root && cd packages/db && npx prisma version >/dev/null && npm cache clean --force'
COPY packages/db/prisma ./prisma
COPY packages/db/prisma.config.ts ./
USER node
CMD ["npx", "prisma", "migrate", "deploy"]

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build --chown=root:root /app/package.json ./
COPY --from=build --chown=root:root /app/node_modules ./node_modules
COPY --from=build --chown=root:root /app/packages/core/package.json packages/core/
COPY --from=build --chown=root:root /app/packages/core/dist packages/core/dist
COPY --from=build --chown=root:root /app/packages/db/package.json packages/db/
COPY --from=build --chown=root:root /app/packages/db/dist packages/db/dist
COPY --from=build --chown=root:root /app/apps/api/package.json apps/api/
COPY --from=build --chown=root:root /app/apps/api/dist apps/api/dist
# Files are root-owned and read-only to the runtime user; only the local document
# directory (development stack) is writable.
RUN mkdir -p /app/.tmp/storage && chown node:node /app/.tmp/storage && chmod 700 /app/.tmp/storage
USER node
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||4000)+'/api/v1/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "--enable-source-maps", "apps/api/dist/main.js"]
