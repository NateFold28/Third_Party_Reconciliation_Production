"""
MONTHLY CALIBRATION REFRESH
============================
Run this immediately after each monthly pipeline/model retrain completes.

What it does:
  1. Queries the most recent N_MONTHS of MATURED VALIDATION contracts
  2. Refits per-(segment × horizon) isotonic regression calibrators
  3. Gates: refuses to commit if ECE or AUC are worse than current baseline
  4. Replaces all rows in STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS
  5. Logs results to console and CALIBRATION_REFRESH_LOG.txt

When to run:
  - Monthly, after SP_ML_SANDBOX_TRAIN() completes and predictions are refreshed
  - Or after any retrain that produces new VALIDATION-split rows in ML_SANDBOX_V5_PREDICTIONS

Scheduling:
  - Can be added to the pipeline scheduler (scheduler/) as a post-train step
  - Or run manually: python recalibrate_monthly.py
  - Dry-run mode: python recalibrate_monthly.py --dry-run

Safety:
  - If any gate fails the EXISTING knots are preserved (no destructive write)
  - Always validates on a held-out month before committing
  - Logs every run whether it writes or not
"""

import sys
import json
import argparse
import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe, get_snowflake_connection as get_connection

# ── Config ────────────────────────────────────────────────────────────────────
KNOT_TABLE  = "STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS"
PREDS_TABLE = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT_TABLE  = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"

N_TRAIN_MONTHS   = 3      # months used for fitting calibrators (rolling)
N_HOLDOUT_MONTHS = 1      # most recent month held out for gate validation
HORIZONS         = list(range(7))  # 0-6
MIN_ROWS_PER_CELL = 50   # skip segment×horizon cell if fewer training rows

# Gate thresholds — if the new calibrators FAIL these on the holdout month,
# the existing knots are kept unchanged.
GATE_ECE_MAX    = 0.06   # must beat this on holdout (current baseline ~0.035)
GATE_AUC_MIN    = 0.68   # must not lose more than this
GATE_ECE_WORSE_TOLERANCE = 0.01  # new ECE can be at most +1pp worse than current

LOG_FILE = _HERE / "CALIBRATION_REFRESH_LOG.txt"

# ── Helpers ───────────────────────────────────────────────────────────────────
def _ece(p, y, w, n_bins=10):
    """Dollar-weighted Expected Calibration Error."""
    df = pd.DataFrame({"p": p, "y": y, "w": w}).dropna()
    if len(df) < 20:
        return np.nan
    try:
        df["bin"] = pd.qcut(df["p"], q=n_bins, duplicates="drop")
    except Exception:
        return np.nan
    by = df.groupby("bin", observed=True).apply(
        lambda x: pd.Series({
            "wt":    x["w"].sum(),
            "p_avg": (x["p"] * x["w"]).sum() / x["w"].sum(),
            "y_avg": (x["y"] * x["w"]).sum() / x["w"].sum(),
        }), include_groups=False
    )
    by["gap"] = (by["p_avg"] - by["y_avg"]).abs()
    return (by["gap"] * by["wt"]).sum() / by["wt"].sum()


def _fit_isotonic(p_train, y_train, w_train=None):
    """Fit isotonic regression, return (knot_x, knot_y) arrays.

    Calibrated to P(event) — monotone increasing mapping.
    """
    mask = ~(np.isnan(p_train) | np.isnan(y_train))
    if mask.sum() < MIN_ROWS_PER_CELL:
        return None, None
    p_t = p_train[mask]
    y_t = y_train[mask]
    wt  = w_train[mask] if w_train is not None else None

    ir = IsotonicRegression(out_of_bounds="clip")
    ir.fit(p_t, y_t, sample_weight=wt)

    # Deduplicate knot points (isotonic can produce many repeated x-values)
    x_full = ir.X_thresholds_
    y_full = ir.y_thresholds_
    # Add boundary clamps
    x_all = np.concatenate([[0.0], x_full, [1.0]])
    y_all = np.concatenate([[y_full[0]], y_full, [y_full[-1]]])
    # Deduplicate
    _, idx = np.unique(x_all, return_index=True)
    return x_all[idx].tolist(), y_all[idx].tolist()


def _apply_knots(p_values, knot_x, knot_y):
    return np.interp(p_values, knot_x, knot_y).clip(0, 1)


# ── Main ──────────────────────────────────────────────────────────────────────
def main(dry_run: bool = False):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    log_lines = [f"\n{'='*80}", f"CALIBRATION REFRESH  {ts}  dry_run={dry_run}", f"{'='*80}"]

    def log(msg):
        print(msg)
        log_lines.append(msg)

    # ── 1. Load data ──────────────────────────────────────────────────────────
    log("Loading validation predictions + actuals...")
    q = f"""
    WITH latest_run AS (
        SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS_TABLE} WHERE SPLIT = 'VALIDATION'
    )
    SELECT
        p.CONTRACT_ID_UFR   AS CONTRACT_ID,
        DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
        p.HORIZON,
        p.SEGMENT,
        p.ATR,
        p.P_LOGO_CHURN,
        p.P_FULL_RENEWAL,
        p.P_DOLLAR_CHURN,
        f.TARGET__RENEWAL_RATE AS ACTUAL_RATE
    FROM {PREDS_TABLE} p
    JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
    JOIN {FEAT_TABLE} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND DATE_TRUNC('MONTH', p.RENEWAL_MONTH) = DATE_TRUNC('MONTH', f.RENEWAL_MONTH)
        AND p.HORIZON = f.HORIZON
        AND p.SPLIT   = f.SPLIT
    WHERE p.SPLIT = 'VALIDATION'
      AND f.COHORT = 'MATURED'
      AND p.ATR > 0
      AND f.TARGET__RENEWAL_RATE IS NOT NULL
    ORDER BY p.RENEWAL_MONTH
    """
    df = fetch_dataframe(q)
    df["RENEWAL_MONTH"] = pd.to_datetime(df["RENEWAL_MONTH"])
    df["ACTUAL_LOGO"]   = (df["ACTUAL_RATE"] == 0.0).astype(float)
    df["ACTUAL_FULL"]   = (df["ACTUAL_RATE"] == 1.0).astype(float)
    df["HORIZON"]       = df["HORIZON"].astype(int)
    for c in ("P_LOGO_CHURN", "P_FULL_RENEWAL", "ATR"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["P_LOGO_CHURN", "P_FULL_RENEWAL", "ATR", "ACTUAL_RATE"])
    log(f"  Loaded {len(df):,} rows across {df['RENEWAL_MONTH'].nunique()} months")

    # ── 2. Split into train and holdout months ────────────────────────────────
    all_months = sorted(df["RENEWAL_MONTH"].unique())
    if len(all_months) < (N_TRAIN_MONTHS + N_HOLDOUT_MONTHS):
        log(f"ERROR: Need at least {N_TRAIN_MONTHS + N_HOLDOUT_MONTHS} months, got {len(all_months)}")
        _write_log(log_lines)
        return

    # Holdout = most recent month(s); train = N_TRAIN_MONTHS before that
    holdout_months = all_months[-N_HOLDOUT_MONTHS:]
    train_months   = all_months[-(N_TRAIN_MONTHS + N_HOLDOUT_MONTHS):-N_HOLDOUT_MONTHS]

    log(f"  Train months:   {[str(m.date()) for m in train_months]}")
    log(f"  Holdout months: {[str(m.date()) for m in holdout_months]}")

    df_train   = df[df["RENEWAL_MONTH"].isin(train_months)].copy()
    df_holdout = df[df["RENEWAL_MONTH"].isin(holdout_months)].copy()
    log(f"  Train rows: {len(df_train):,} | Holdout rows: {len(df_holdout):,}")

    # ── 3. Fetch current knots (for gate comparison) ──────────────────────────
    log("\nLoading current knots from Snowflake...")
    try:
        cur_df = fetch_dataframe(f"SELECT MODEL_TARGET, SEGMENT, HORIZON, KNOT_X_JSON, KNOT_Y_JSON FROM {KNOT_TABLE}")
        current_knots = {}
        for _, row in cur_df.iterrows():
            key = (str(row["MODEL_TARGET"]), str(row["SEGMENT"]), int(row["HORIZON"]))
            current_knots[key] = {
                "x": json.loads(row["KNOT_X_JSON"]),
                "y": json.loads(row["KNOT_Y_JSON"]),
            }
        log(f"  Current knots: {len(current_knots)} entries")
    except Exception as e:
        log(f"  WARNING: Could not load current knots: {e}")
        current_knots = {}

    # ── 4. Fit new calibrators ────────────────────────────────────────────────
    log("\nFitting isotonic calibrators per (segment × horizon)...")
    segments  = sorted(df_train["SEGMENT"].dropna().unique())
    new_knots = {}
    skipped   = 0

    for seg in segments:
        for h in HORIZONS:
            sub = df_train[(df_train["SEGMENT"] == seg) & (df_train["HORIZON"] == h)]
            if len(sub) < MIN_ROWS_PER_CELL:
                skipped += 1
                continue

            for target, col_p, col_y in [
                ("P_LOGO_CHURN",    "P_LOGO_CHURN",    "ACTUAL_LOGO"),
                ("P_FULL_RENEWAL",  "P_FULL_RENEWAL",  "ACTUAL_FULL"),
            ]:
                kx, ky = _fit_isotonic(
                    sub[col_p].values,
                    sub[col_y].values,
                    sub["ATR"].values,
                )
                if kx is None:
                    skipped += 1
                    continue
                new_knots[(target, seg, h)] = {"x": kx, "y": ky}

    log(f"  Fitted: {len(new_knots)} calibrators | Skipped (insufficient data): {skipped}")

    # ── 5. Gate validation on holdout ─────────────────────────────────────────
    log("\nValidating new calibrators on holdout month...")

    def _score_frame(df_h, knots_to_use, label):
        p_logo_cal = df_h["P_LOGO_CHURN"].values.copy().astype(float)
        p_full_cal = df_h["P_FULL_RENEWAL"].values.copy().astype(float)
        for (seg, h), grp in df_h.groupby(["SEGMENT", "HORIZON"]):
            h_int = int(min(max(h, 0), 6))
            mask  = df_h.index.get_indexer(grp.index)
            for target, arr in [("P_LOGO_CHURN", p_logo_cal), ("P_FULL_RENEWAL", p_full_cal)]:
                k = knots_to_use.get((target, seg, h_int))
                if k:
                    arr[mask] = np.interp(grp[target.replace("P_LOGO_CHURN","P_LOGO_CHURN").replace("P_FULL_RENEWAL","P_FULL_RENEWAL")].values.astype(float), k["x"], k["y"]).clip(0, 1)
        return {
            "ece_logo": _ece(p_logo_cal, df_h["ACTUAL_LOGO"].values, df_h["ATR"].values),
            "ece_full": _ece(p_full_cal, df_h["ACTUAL_FULL"].values, df_h["ATR"].values),
            "auc_logo": roc_auc_score(df_h["ACTUAL_LOGO"], p_logo_cal) if df_h["ACTUAL_LOGO"].sum() > 5 else np.nan,
            "auc_full": roc_auc_score(df_h["ACTUAL_FULL"], p_full_cal) if df_h["ACTUAL_FULL"].sum() > 5 else np.nan,
        }

    new_scores = _score_frame(df_holdout, new_knots, "NEW")
    old_scores = _score_frame(df_holdout, current_knots, "OLD") if current_knots else {}

    log(f"  {'Metric':<20}{'New':>10}{'Old':>10}{'Gate':>10}")
    log(f"  {'-'*50}")
    for k, v in new_scores.items():
        old = old_scores.get(k, np.nan)
        gate = "PASS" if not np.isnan(v) and (
            (k.startswith("ece") and v < GATE_ECE_MAX) or
            (k.startswith("auc") and v > GATE_AUC_MIN)
        ) else "FAIL"
        log(f"  {k:<20}{v:>10.4f}{old:>10.4f}{gate:>10}")

    gate_pass = (
        (not np.isnan(new_scores["ece_full"])) and
        new_scores["ece_full"] < GATE_ECE_MAX and
        (not np.isnan(new_scores["auc_full"])) and
        new_scores["auc_full"] > GATE_AUC_MIN and
        (not np.isnan(new_scores["ece_logo"])) and
        new_scores["ece_logo"] < GATE_ECE_MAX
    )

    # Also check: are new knots worse than current by more than tolerance?
    if current_knots and not np.isnan(old_scores.get("ece_full", np.nan)):
        regression_gap = new_scores["ece_full"] - old_scores["ece_full"]
        if regression_gap > GATE_ECE_WORSE_TOLERANCE:
            log(f"\n  *** REGRESSION: new ECE_FULL {new_scores['ece_full']:.4f} is "
                f"{regression_gap:.4f} worse than current {old_scores['ece_full']:.4f}")
            gate_pass = False

    if not gate_pass:
        log("\n!!! GATE FAILED — existing knots preserved unchanged !!!")
        log("    Review data quality and retrain with more history if needed.")
        _write_log(log_lines)
        return

    log("\n  ✓ All gates passed")

    # ── 6. Commit new knots to Snowflake ──────────────────────────────────────
    if dry_run:
        log(f"\nDRY RUN — would write {len(new_knots)} knot rows to {KNOT_TABLE}")
        _write_log(log_lines)
        return

    log(f"\nWriting {len(new_knots)} knot rows to {KNOT_TABLE}...")
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(f"TRUNCATE TABLE IF EXISTS {KNOT_TABLE}")
        rows = []
        for (target, seg, h), pts in new_knots.items():
            rows.append((
                target, seg, int(h),
                json.dumps([round(float(v), 6) for v in pts["x"]]),
                json.dumps([round(float(v), 6) for v in pts["y"]]),
                len(pts["x"]),
                f"Fitted on {[str(m.date()) for m in train_months]}",
                ts,
            ))
        cur.executemany(
            f"""INSERT INTO {KNOT_TABLE}
                (MODEL_TARGET, SEGMENT, HORIZON, KNOT_X_JSON, KNOT_Y_JSON,
                 N_KNOTS, DESCRIPTION, INSERTED_AT)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )
        conn.commit()
        verify = fetch_dataframe(f"SELECT COUNT(*) AS N FROM {KNOT_TABLE}")
        n_written = int(verify.iloc[0, 0])
        log(f"  Verified {n_written} rows in Snowflake")
        log(f"  ECE_FULL (new): {new_scores['ece_full']:.4f}  "
            f"AUC_FULL (new): {new_scores['auc_full']:.3f}")
        log("\nCALIBRATION REFRESH COMPLETE ✓")
    except Exception as e:
        log(f"ERROR writing to Snowflake: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    _write_log(log_lines)


def _write_log(lines):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nLog appended to: {LOG_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monthly isotonic calibration refresh")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fit and validate but do NOT write to Snowflake")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
