# syntax=docker/dockerfile:1.7
# Build the static dashboard, then serve it with Nginx (no Node.js at runtime).
FROM node:22.20.0-alpine AS build
WORKDIR /app
COPY apps/dashboard/package.json apps/dashboard/package-lock.json apps/dashboard/.npmrc ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi \
 && npm ci --no-audit --no-fund
COPY apps/dashboard ./
RUN NEXT_TELEMETRY_DISABLED=1 npx next build

FROM nginx:1.27.2-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY infrastructure/nginx/nexus.conf.template /etc/nginx/templates/nexus.conf.template
COPY infrastructure/nginx/proxy_params.conf /etc/nginx/proxy_params.conf
COPY --from=build /app/out /usr/share/nginx/html
