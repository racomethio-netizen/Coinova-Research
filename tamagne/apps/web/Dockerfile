# syntax=docker/dockerfile:1.7
# Build context: the tamagne/ directory. Behind a TLS-intercepting proxy, add
#   --secret id=npm_ca,src=/path/to/proxy-ca.pem  (never baked into the image)
# Build context: the tamagne/ directory.  docker build -f apps/web/Dockerfile -t tamagne-web .
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json .npmrc tsconfig.base.json ./
COPY packages/core/package.json packages/core/
COPY packages/db/package.json packages/db/
COPY apps/api/package.json apps/api/
COPY apps/web/package.json apps/web/
RUN --mount=type=secret,id=npm_ca,required=false \
    sh -c '[ -f /run/secrets/npm_ca ] && export NPM_CONFIG_CAFILE=/run/secrets/npm_ca; npm ci --workspace @tamagne/web --include-workspace-root'
COPY apps/web apps/web
RUN npm run build -w @tamagne/web

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=root:root /app/apps/web/.next/standalone ./
COPY --from=build --chown=root:root /app/apps/web/.next/static apps/web/.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD node -e "fetch('http://127.0.0.1:3000/en').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "apps/web/server.js"]
