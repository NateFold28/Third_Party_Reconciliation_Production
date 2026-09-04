from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_outcomes import strict_outcome_case, structural_evidence_case


class StrictOutcomeCaseTests(unittest.TestCase):
    def test_contains_required_strict_branches(self) -> None:
        sql = strict_outcome_case()

        self.assertIn("THEN 'Clear'", sql)
        self.assertIn("THEN 'API Usage, Insufficient CW Billing'", sql)
        self.assertIn("THEN 'Vendor Billing, No CW Billing'", sql)
        self.assertIn("THEN 'Vendor Billing, Insufficient CW Billing'", sql)
        self.assertIn("THEN 'CW Billing, No Vendor Billing'", sql)
        self.assertNotIn("Other Issue", sql)

    def test_uses_point_in_time_api_only(self) -> None:
        sql = strict_outcome_case(
            api_quantity="POINT_API",
            avg_api_quantity="EXPLORATORY_AVG_API",
        )

        self.assertIn("POINT_API", sql)
        self.assertNotIn("EXPLORATORY_AVG_API", sql)

    def test_marketplace_then_clear_then_mapping_precedence(self) -> None:
        sql = strict_outcome_case()

        marketplace = sql.index("THEN 'Marketplace Billing Delay'")
        clear = sql.index("THEN 'Clear'")
        unmapped = sql.index("THEN 'Unmapped Partner'")
        self.assertLess(marketplace, clear)
        self.assertLess(clear, unmapped)

    def test_excludes_legacy_partner_month_rollup_and_thresholds(self) -> None:
        sql = strict_outcome_case()
        upper_sql = sql.upper()

        self.assertNotIn("OVER (PARTITION BY", upper_sql)
        self.assertNotIn("* 1.25", sql)
        self.assertNotIn("VENDOR_QUANTITY", upper_sql)
        self.assertNotIn("TOTAL_BILLING_QUANTITY", upper_sql)

    def test_duplicate_primary_bucket_is_not_active(self) -> None:
        sql = strict_outcome_case()

        # Duplicate remains a side flag only. Any duplicate primary bucket line
        # must remain commented-out in the generated CASE.
        self.assertNotIn("\n    THEN 'Duplicated CW Invoice'", sql)

    def test_structural_evidence_is_typed_and_suffix_safe(self) -> None:
        evidence_sql = structural_evidence_case("NATIVE_OUTCOME_EVIDENCE")
        classifier_sql = strict_outcome_case()

        self.assertIn("SPLIT_PART", evidence_sql)
        self.assertIn("THEN 'MARKETPLACE_BILLING_DELAY'", evidence_sql)
        self.assertIn("THEN 'UNMAPPED_PARTNER'", evidence_sql)
        self.assertNotIn("KNOWN_DISCOUNT_BUNDLE", evidence_sql)
        self.assertNotIn("DISABLED_PARTNER_SKU'", evidence_sql)
        self.assertIn("STRUCTURAL_EVIDENCE_CODE", classifier_sql)
        self.assertNotIn("SPLIT_PART", classifier_sql)

    def test_pipeline_and_app_use_single_source(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        skeleton = (repo / "Reconciliation" / "_run_skeleton_pipeline.py").read_text(
            encoding="utf-8"
        )
        builder = (
            repo / "Reconciliation" / "build_third_party_recon_output_prod.py"
        ).read_text(encoding="utf-8")
        app = (repo / "app" / "combined_recon_app.py").read_text(encoding="utf-8")

        self.assertIn("from canonical_outcomes import strict_outcome_case", skeleton)
        self.assertIn("POST_OVERLAY_STRICT_RECLASS_SQL", skeleton)
        self.assertIn("from canonical_outcomes import strict_outcome_case", builder)
        self.assertIn("OUTCOME_FLAG                                       AS EXCEPTION_TYPE", builder)
        self.assertIn("COALESCE(VENDOR_AMOUNT, 0) = 0", builder)
        self.assertIn("COALESCE(TOTAL_BILLING_AMOUNT, 0) = 0", builder)
        self.assertIn("not reconciliation cases and must not enter OUTPUT_PROD", builder)
        self.assertNotIn("SOLE_ACCOUNT_MONTH_CASE", builder)
        self.assertIn("UNRESOLVED_NO_EXACT_SKU", builder)
        self.assertNotIn("FLAG_PLAIN", app)
        self.assertNotIn("BUCKET_QTY_TOLERANCE_PCT", app)
        self.assertIn('if "EXCEPTION_TYPE" not in detail.columns:', app)
        self.assertIn('raise ValueError("Published reconciliation data is missing EXCEPTION_TYPE")', app)
        self.assertIn("Invalid canonical EXCEPTION_TYPE value(s)", app)

    def test_publication_fails_closed_and_preserves_native_grain(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        skeleton = (repo / "Reconciliation" / "_run_skeleton_pipeline.py").read_text(
            encoding="utf-8"
        )
        builder = (
            repo / "Reconciliation" / "build_third_party_recon_output_prod.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"Exium": "SKU_MATCH_GROUP"', skeleton)
        self.assertIn('"KeepIT": "SOURCE_FAMILY"', skeleton)
        self.assertIn('"Webroot": "RECON_STREAM"', skeleton)
        self.assertIn("RECON_SUBGRAIN", skeleton)
        self.assertIn("ABORTING: staged detail will not be published", skeleton)
        self.assertIn("def require_sql", builder)
        self.assertEqual(builder.count("require_sql(conn,"), 5)

        case_id_block = builder.split("CONCAT_WS(", 1)[1].split("AS CASE_ID", 1)[0]
        self.assertIn("c.RECON_SUBGRAIN", case_id_block)
        self.assertIn("c.VENDOR_PARTNER_NAME", case_id_block)
        self.assertNotIn("c.EXCEPTION_TYPE", case_id_block)

    def test_full_refresh_wires_invoice_enrichment_and_refreshes_by_default(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        full_runner = (repo / "Reconciliation" / "_run_full_refresh_pipeline.py").read_text(
            encoding="utf-8"
        )
        skeleton = (repo / "Reconciliation" / "_run_skeleton_pipeline.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"--enable-smart-skip"', full_runner)
        self.assertIn("args.enable_smart_skip", full_runner)
        self.assertIn("AUTHORITATIVE_TABLES", full_runner)
        self.assertIn("verify_authoritative_freshness(run_started_at)", full_runner)
        self.assertIn('state["last_run_status"] = "partial_or_smart_skipped"', full_runner)
        self.assertIn(r'Maps\sql\00b_backfill_invoice_prices.sql', skeleton)
        self.assertLess(
            skeleton.index(r'Maps\sql\00b_backfill_invoice_prices.sql'),
            skeleton.index("=== STEP 1a: run live vendor SQL files"),
        )
        self.assertIn("LINK INTEGRITY GATE: FAIL", skeleton)

    def test_recon_team_full_output_export_is_unfiltered_and_complete(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        builder = (
            repo / "Reconciliation" / "build_third_party_recon_output_prod.py"
        ).read_text(encoding="utf-8")
        app = (repo / "app" / "combined_recon_app.py").read_text(encoding="utf-8")

        for output_column in (
            "CMS_ID", "CW_PARTNER_NAME", "CW_PARENT_COMPANY",
            "VENDOR_PRODUCT_SKU", "CW_SKU", "MATCHED_INVOICE_SKU",
        ):
            self.assertIn(f"AS {output_column}", builder)
        for export_column in (
            "Vendor", "Billing Month", "Invoice ID", "Vendor Partner Name",
            "SF ID", "CMS ID", "CW Partner Name", "CW Parent Company",
            "Vendor Product SKU", "CW SKU", "Vendor Qty", "Vendor Unit Price",
            "Vendor Amount", "API Qty", "Avg API Qty", "Zuora Qty",
            "Zuora Unit Price", "Zuora Amount", "MP Qty", "MP Unit Price",
            "MP Amount", "CW Total Billing Qty", "CW Total Billing Amount",
            "Qty Delta", "Amount Delta", "Outcome Flag", "Investigation Reason",
            "SF Account URL", "Case ID",
        ):
            self.assertIn(f'"{export_column}":', app)
        self.assertIn("_load_all_recon_frames(", app)
        self.assertIn("Click a row to inspect its complete reconciliation record.", app)
        self.assertIn('"CW SKU": source("MATCHED_INVOICE_SKU", "ZUORA_SKUS", "MARKETPLACE_SKUS")', app)
        self.assertIn('key="actual_reconciliation_table"', app)
        self.assertIn('selection_mode="single-row"', app)
        self.assertEqual(app.count('<table class="recon"'), 2)
        self.assertIn("def _invoice_links(link_keys: object, invoice_ids: object)", app)
        self.assertNotIn('"Invoice Link": st.column_config.LinkColumn', app)
        self.assertIn('label="Download Actual Reconciliation as CSV"', app)
        self.assertNotIn('f"resolver: {freshness', app)
        self.assertIn("fmt_est_timestamp(latest_freshness_timestamp(freshness))", app)

    def test_confirmed_source_fanout_controls_are_present(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        webroot = (repo / "Reconciliation" / "Webroot_Reconciliation_Script_Prod.sql").read_text(
            encoding="utf-8"
        )
        acronis = (repo / "Reconciliation" / "Acronis_Reconciliation_Script_Prod.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("webroot_trt_partner_map AS", webroot)
        self.assertIn("PARTITION BY billing_month, cms_id", webroot)
        self.assertNotIn("CROSS JOIN (SELECT column1 AS stream FROM VALUES ('CMS'),('CW'))", webroot)
        self.assertIn("m.transaction_source", acronis)
        self.assertIn("m.marketplace_invoice_id", acronis)

    def test_invoice_usage_control_has_vendor_specific_alignment(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        intra = (
            repo / "Reconciliation" / "10_vendor_invoice_usage_intra_prod.sql"
        ).read_text(encoding="utf-8")
        invoice_ingestion = (
            repo / "Ingestion" / "Netsuite_Invoice_JSON_Ingestion_Prod.py"
        ).read_text(encoding="utf-8")

        self.assertIn("auvik_partner_bridge AS", intra)
        self.assertIn("'^(OVERAGE)?ANM(ESSENTIALS|PERFORMANCEADDONS?)EVERGREEN$'", intra)
        self.assertIn("s.raw_sku ILIKE '%ATS & EDR%' THEN 'ATS EDR'", intra)
        self.assertIn("s.raw_sku ILIKE '%Advanced Threat Security%' THEN 'ATS'", intra)
        self.assertIn("s.raw_sku_key = 'BP 2765 ME LOY' THEN 'EMAIL'", intra)
        self.assertIn("if int(mo.month) > fallback_month:", invoice_ingestion)


if __name__ == "__main__":
    unittest.main()
