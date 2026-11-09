# Compose smoke test — 2026-11-09

Images `tamagne-{api,migrate,web}:p8` built from this commit's Dockerfiles. Command: `TAMAGNE_TAG=p8 bash ops/compose-smoke.sh`.
API ran with `APP_ENV=staging`, SMS/storage/scanning disabled (no production providers yet).

```
▶ images tamagne-*:p8
  ✅ migrations apply (migrate job)
  ✅ API ready
  ✅ web serves /en
▶ HTTP
  ✅ web → API proxy (/api/v1/health via web)
  ✅ web sends nonce CSP
  ✅ web sends X-Content-Type-Options
  ✅ unknown seller → 404 envelope with requestId
  ✅ OpenAPI UI disabled
  ✅ Fayda integration reports disabled
  ✅ metrics refuse missing token
  ✅ metrics with token
  ✅ session cookies are Secure/__Host- (staging)
▶ containers
  ✅ API runs as non-root (uid 1000)
  ✅ web runs as non-root (uid 1000)
  ✅ API filesystem is read-only
  ✅ API logs are JSON lines
  ✅ access log carries route template, not the ID
  ✅ job: expiry
  ✅ job: retention
  ✅ job: deletions
  ✅ job: rekey
  ✅ audit chain intact
  ✅ jobs wrote audit entries
▶ result: 23 passed, 0 failed
```

Image sizes (uncompressed / content): api 449 MB / 96.5 MB (was 523 / 117), web 426 MB / 105 MB, migrate 2.25 GB / 547 MB (one-off job; full Debian image for Prisma's OpenSSL dependency).

## ClamAV (real daemon)

`clamav/clamav:1.4` (ClamAV 1.4.6, bundled signatures daily 28177 of 2026-11-08) with `CLAMAV_TEST_HOST=127.0.0.1 npx vitest run test/adapters.test.ts`: EICAR detected as `Eicar-Test-Signature`, clean PDF passed, 8 MB document scanned in ~0.8 s. **freshclam could not reach database.clamav.net from this environment**, so signatures would go stale: production needs outbound access to the ClamAV mirror (or a local mirror) and an alert on signature age.
