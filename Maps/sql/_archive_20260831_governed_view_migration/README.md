# Governed Map View Migration — 2026-08-31

## Outcome (final)

Hybrid layout: 3 governed **tables** that auto-rebuild every pipeline run + 1
governed **view** over the seed. Same objects, same names, same vendor SQL.

| Object                          | Prior state | Final state | Notes |
| ------------------------------- | ----------- | ----------- | ----- |
| `RECON_ACCOUNT_MERGE_RESOLVER`  | TABLE       | TABLE       | Auto-rebuilt in skeleton STEP 0a |
| `RECON_PARTNER_MAP`             | TABLE       | TABLE       | Auto-rebuilt in skeleton STEP 0a |
| `RECON_PARTNER_MAP_MONTHLY`     | TABLE       | TABLE       | Auto-rebuilt in skeleton STEP 0a |
| `RECON_SKU_MAP`                 | TABLE       | **VIEW**    | Live over seed + pricebook       |
| `V_RECON_PARTNER_MAP_MONTHLY_NORM` | VIEW     | VIEW        | Unchanged                        |
| `V_RECON_PRICEBOOK_TIER_LOOKUP` | VIEW        | VIEW        | Unchanged                        |
| `RECON_VENDOR_PARTNER_MANUAL_MAP` | TABLE     | TABLE       | Manually populated; unchanged    |

## Why hybrid instead of all-views

The first pass of this migration converted all 4 tables to live views. Proofpoint
SQL then ran for 3.5+ minutes without finishing (baseline ~50s) because the
recursive walk of `CW_DW__MERGED_ACCOUNT_MAP` in
`RECON_ACCOUNT_MERGE_RESOLVER` fires on every join across every vendor SQL.
The all-views run was cancelled and reverted.

Trade-off chosen:
- Auto-rebuild in pipeline STEP 0a (~9s total) picks up seed edits automatically.
- Vendor SQL still joins to materialized tables → fast query execution.
- User-facing behaviour matches the "seed edits are always live" goal: every
  pipeline run starts by rebuilding the governed layer from the seed.

## Skeleton pipeline changes

`_run_skeleton_pipeline.py` — added STEP 0a that runs
`Maps/sql/02_unified_reference_maps.sql` at pipeline start.

## Parity vs baseline

Baseline (`THIRD_PARTY_RECON_OUTPUT_PROD_BAK_20260831`): 92,500 rows, 66,675 clear, 147 unmapped-partner.
Post-migration: 92,492 rows, **66,741 clear (+66)**, **119 unmapped-partner (-28)**.

Every vendor either matched or improved. No vendor regressed. Proofpoint parity
gate: **PASS (95.7%)**.

## Backups

Snapshotted 2026-08-31 before the migration:

```
RECON_ACCOUNT_MERGE_RESOLVER_BACKUP_20260831   (5,115 rows)
RECON_PARTNER_MAP_BACKUP_20260831              (7,210 rows)
RECON_PARTNER_MAP_MONTHLY_BACKUP_20260831      (1,730,400 rows)
RECON_SKU_MAP_BACKUP_20260831                  (651 rows)
RECON_VENDOR_PARTNER_MANUAL_MAP_BACKUP_20260831 (2,469 rows)
THIRD_PARTY_RECON_OUTPUT_PROD_BAK_20260831     (92,500 rows)
THIRD_PARTY_RECON_SUMMARY_PROD_BAK_20260831    (85 rows)
THIRD_PARTY_RECON_DETAIL_PROD_BAK_20260831     (92,710 rows)
```

Keep for at least one week of pipeline runs to verify sustained parity before dropping.

## Rollback

1. Revert `_run_skeleton_pipeline.py` STEP 0a change (remove the new
   `run_repo_sql_file(..., "02_unified_reference_maps.sql", ...)` block).
2. Restore the pre-migration SQL body:
   ```powershell
   Copy-Item PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline\Maps\sql\_archive_20260831_governed_view_migration\02_unified_reference_maps_PRE_VIEW_MIGRATION.sql PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline\Maps\sql\02_unified_reference_maps.sql
   ```
3. If needed, promote the backups back in Snowflake:
   ```sql
   DROP VIEW  IF EXISTS RECON_SKU_MAP;
   CREATE OR REPLACE TABLE RECON_ACCOUNT_MERGE_RESOLVER AS SELECT * FROM RECON_ACCOUNT_MERGE_RESOLVER_BACKUP_20260831;
   CREATE OR REPLACE TABLE RECON_PARTNER_MAP           AS SELECT * FROM RECON_PARTNER_MAP_BACKUP_20260831;
   CREATE OR REPLACE TABLE RECON_PARTNER_MAP_MONTHLY   AS SELECT * FROM RECON_PARTNER_MAP_MONTHLY_BACKUP_20260831;
   CREATE OR REPLACE TABLE RECON_SKU_MAP               AS SELECT * FROM RECON_SKU_MAP_BACKUP_20260831;
   ```

## Post-board follow-up (not done tonight)

- Fold `RECON_VENDOR_PARTNER_MANUAL_MAP` (2,469 rows) into `THIRD_PARTY_RECON_PARTNER_MAP_PROD`
  seed table and retire it.
- Consider inlining the merge-resolver + normalization logic into each vendor
  SQL and dropping `RECON_PARTNER_MAP*` entirely. This is a large rewrite and
  will regress parity unless carefully tested per vendor.
