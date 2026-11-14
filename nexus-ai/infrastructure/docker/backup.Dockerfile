# syntax=docker/dockerfile:1.7
FROM postgres:16.4-alpine
# Optional extra CA for TLS-inspecting proxies (BuildKit secret, never stored in a layer).
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi \
 && apk add --no-cache gnupg
RUN mkdir -p /backups && chown postgres:postgres /backups && chmod 700 /backups
COPY infrastructure/backup/backup.sh /usr/local/bin/nexus-backup
USER postgres
ENTRYPOINT ["/usr/local/bin/nexus-backup"]
