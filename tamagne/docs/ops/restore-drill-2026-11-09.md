# Restore drill — 2026-11-09

Result: **PASSED** (2026-11-09T16:29:37.556Z)

| Check | Result | Detail |
|-------|--------|--------|
| Backup created and encrypted | ✅ | 429 KiB in 227 ms |
| Backup contains no plaintext SQL | ✅ |  |
| Restore completed | ✅ | 449 ms |
| Row counts identical in every table | ✅ | 27 tables, 3286 rows |
| All migrations recorded as applied | ✅ | 7/7 (repository has 7) |
| Audit hash chain intact after restore | ✅ | intact |
| Integrity triggers restored | ✅ | 3/3 |
| Tampered backup is rejected (GCM authentication) | ✅ |  |
| API ready against the restored database | ✅ |  |

Source: `postgresql://***@127.0.0.1:54339/tamagne_test` · scratch database `tamagne_restore_drill` (dropped afterwards).
Backup 227 ms, restore 449 ms. Recovery time for this data size: ~1 s
(scale with production size; measure again on production-like hardware).
