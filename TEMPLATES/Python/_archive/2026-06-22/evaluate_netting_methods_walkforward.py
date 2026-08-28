"""Evaluate candidate netting methods for board-level forecast accuracy.

Goal:
- Compare portfolio->contract netting methods using walk-forward evaluation.
- Use only information available before each target month.
- Report MAE/RMSE/Bias versus actual monthly contract-level outcomes.

Run:
  c:/Users/Nate.Fold/projects/.venv/Scripts/python.exe TEMPLATES/Python/evaluate_netting_methods_walkforward.py
"""

from __future__ import annotations

import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


def fetch_df(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def _trimmed_mean(vals: pd.Series, min_n: int = 3) -> float | None:
    s = pd.to_numeric(vals, errors="coerce").dropna().astype(float)
    if len(s) < min_n:
        return None
    if len(s) >= 5:
        s = s.sort_values().iloc[1:-1]  # drop one min + one max
    return float(s.mean())


def _winsor_mean(vals: pd.Series, min_n: int = 3, lower_q: float = 0.1, upper_q: float = 0.9) -> float | None:
    s = pd.to_numeric(vals, errors="coerce").dropna().astype(float)
    if len(s) < min_n:
        return None
    lo = float(s.quantile(lower_q))
    hi = float(s.quantile(upper_q))
    return float(s.clip(lower=lo, upper=hi).mean())


def _ewma(vals: pd.Series, halflife: float = 3.0, min_n: int = 3) -> float | None:
    s = pd.to_numeric(vals, errors="coerce").dropna().astype(float)
    if len(s) < min_n:
        return None
    return float(s.ewm(halflife=halflife, adjust=False).mean().iloc[-1])


def _safe_rate(num: float, den: float) -> float | None:
    if den is None or den == 0 or pd.isna(den):
        return None
    return float(num / den * 100.0)


def estimate_netting(method: str, target_month: pd.Timestamp, gap_hist: pd.DataFrame, global_fallback: float) -> float:
    """Compute netting estimate for target month using ONLY prior months."""
    hist = gap_hist[gap_hist["MONTH"] < target_month].copy()
    if hist.empty:
        return global_fallback

    hist = hist.sort_values("MONTH")
    gaps = hist["GAP_PP"].astype(float)

    if method == "flat_1p6":
        return 1.6

    if method == "trailing12_mean":
        recent = hist.tail(12)["GAP_PP"]
        return float(recent.mean()) if len(recent) >= 4 else global_fallback

    if method == "current_app_style_trailing3":
        recent = hist.tail(3)["GAP_PP"]
        return float(recent.mean()) if len(recent) >= 2 else global_fallback

    if method == "finance6_trim":
        recent = hist.tail(6)["GAP_PP"]
        tm = _trimmed_mean(recent, min_n=3)
        return tm if tm is not None else global_fallback

    if method == "finance6_median":
        recent = hist.tail(6)["GAP_PP"]
        if len(recent.dropna()) < 3:
            return global_fallback
        return float(recent.median())

    if method == "finance6_ewma_h3":
        recent = hist.tail(6)["GAP_PP"]
        em = _ewma(recent, halflife=3.0, min_n=3)
        return em if em is not None else global_fallback

    if method == "winsor12_then_recent6":
        recent12 = hist.tail(12)["GAP_PP"]
        w12 = _winsor_mean(recent12, min_n=4, lower_q=0.1, upper_q=0.9)
        t6 = _trimmed_mean(hist.tail(6)["GAP_PP"], min_n=3)
        if w12 is None and t6 is None:
            return global_fallback
        if w12 is None:
            return float(t6)
        if t6 is None:
            return float(w12)
        # Favor recent behavior while anchoring to robust annual level.
        return float(0.7 * t6 + 0.3 * w12)

    if method == "adaptive_recent_plus_longrun":
        recent6 = hist.tail(6)["GAP_PP"]
        r6 = _trimmed_mean(recent6, min_n=3)
        long12 = _trimmed_mean(hist.tail(12)["GAP_PP"], min_n=4)
        if r6 is None and long12 is None:
            return global_fallback
        if r6 is None:
            return float(long12)
        if long12 is None:
            return float(r6)
        # Higher recent volatility -> more shrinkage to long-run.
        vol = float(pd.to_numeric(recent6, errors="coerce").dropna().std(ddof=0) or 0.0)
        w_recent = 0.8 if vol <= 0.75 else (0.65 if vol <= 1.25 else 0.5)
        return float(w_recent * r6 + (1.0 - w_recent) * long12)

    if method == "seasonal_shrink_24":
        recent6 = _trimmed_mean(hist.tail(6)["GAP_PP"], min_n=3)
        long12 = _trimmed_mean(hist.tail(12)["GAP_PP"], min_n=4)
        recent6 = recent6 if recent6 is not None else global_fallback
        long12 = long12 if long12 is not None else recent6

        mo = int(target_month.month)
        seasonal_pool = hist[hist["MONTH"].dt.month == mo].tail(24)["GAP_PP"]
        seasonal = _trimmed_mean(seasonal_pool, min_n=2)
        if seasonal is None:
            seasonal = long12

        n_seas = int(pd.to_numeric(seasonal_pool, errors="coerce").dropna().shape[0])
        w_seas = min(0.4, n_seas / (n_seas + 10.0))
        base = 0.7 * recent6 + 0.3 * long12
        return float((1.0 - w_seas) * base + w_seas * seasonal)

    if method == "huber_recent6":
        s = pd.to_numeric(hist.tail(6)["GAP_PP"], errors="coerce").dropna().astype(float)
        if len(s) < 3:
            return global_fallback
        med = float(s.median())
        mad = float(np.median(np.abs(s - med)))
        if mad == 0:
            return med
        c = 1.5 * mad
        z = (s - med) / c
        w = 1.0 / np.maximum(1.0, np.abs(z))
        return float(np.average(s, weights=w))

    if method == "robust_ewma_winsor":
        s12 = pd.to_numeric(hist.tail(12)["GAP_PP"], errors="coerce").dropna().astype(float)
        if len(s12) < 4:
            return global_fallback
        lo = float(s12.quantile(0.10))
        hi = float(s12.quantile(0.90))
        sw = s12.clip(lower=lo, upper=hi)
        ew = sw.ewm(halflife=2.5, adjust=False).mean().iloc[-1]
        return float(ew)

    if method == "blended_6_trim_plus_seasonal":
        recent6 = hist.tail(6)["GAP_PP"]
        base = _trimmed_mean(recent6, min_n=3)
        if base is None:
            base = global_fallback

        month_num = int(target_month.month)
        seasonal_pool = hist[hist["MONTH"].dt.month == month_num].tail(6)["GAP_PP"]
        seasonal = _trimmed_mean(seasonal_pool, min_n=2)
        if seasonal is None:
            seasonal = base

        # Shrink seasonal effect when limited seasonal history.
        n_seasonal = int(seasonal_pool.notna().sum())
        w_seasonal = min(0.35, n_seasonal / (n_seasonal + 8.0))
        return float((1.0 - w_seasonal) * base + w_seasonal * seasonal)

    raise ValueError(f"Unknown method: {method}")


def evaluate() -> None:
    conn = get_snowflake_connection()
    try:
        bt = fetch_df(
            conn,
            """
            SELECT
              RENEWAL_MONTH,
              SUM(ATR) AS ATR,
              SUM(PREDICTED_RETAINED) AS PRED_PORTFOLIO,
              SUM(PREDICTED_RETAINED_CONTRACT) AS PRED_CONTRACT,
              SUM(ACTUAL_RETAINED) AS ACTUAL
            FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST
            WHERE RENEWAL_MONTH >= '2021-02-01'
            GROUP BY RENEWAL_MONTH
            ORDER BY RENEWAL_MONTH
            """,
        )

        contract_monthly = fetch_df(
            conn,
            """
            SELECT RENEWAL_MONTH, CONTRACT_RATE_PCT
            FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
            WHERE RENEWAL_MONTH >= '2021-02-01'
            ORDER BY RENEWAL_MONTH
            """,
        )

        prod_monthly = fetch_df(
            conn,
            """
            SELECT RENEWAL_MONTH, ATR_PROD, ACTUAL_PROD
            FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED
            WHERE RENEWAL_MONTH >= '2021-02-01'
            ORDER BY RENEWAL_MONTH
            """,
        )

        bt["MONTH"] = pd.to_datetime(bt["RENEWAL_MONTH"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        bt = bt.dropna(subset=["MONTH"]).copy()

        for col in ("ATR", "PRED_PORTFOLIO", "PRED_CONTRACT", "ACTUAL"):
            bt[col] = pd.to_numeric(bt[col], errors="coerce")

        bt["PORT_PRED_PCT"] = np.where(bt["ATR"] > 0, bt["PRED_PORTFOLIO"] / bt["ATR"] * 100.0, np.nan)
        bt["CONTRACT_PRED_PCT"] = np.where(bt["ATR"] > 0, bt["PRED_CONTRACT"] / bt["ATR"] * 100.0, np.nan)
        bt["ACTUAL_PCT"] = np.where(bt["ATR"] > 0, bt["ACTUAL"] / bt["ATR"] * 100.0, np.nan)

        contract_monthly["MONTH"] = pd.to_datetime(contract_monthly["RENEWAL_MONTH"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        contract_monthly["CONTRACT_RATE_PCT"] = pd.to_numeric(contract_monthly["CONTRACT_RATE_PCT"], errors="coerce")

        prod_monthly["MONTH"] = pd.to_datetime(prod_monthly["RENEWAL_MONTH"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        prod_monthly["ATR_PROD"] = pd.to_numeric(prod_monthly["ATR_PROD"], errors="coerce")
        prod_monthly["ACTUAL_PROD"] = pd.to_numeric(prod_monthly["ACTUAL_PROD"], errors="coerce")
        prod_monthly["PORT_ACTUAL_PCT"] = np.where(
            prod_monthly["ATR_PROD"] > 0,
            prod_monthly["ACTUAL_PROD"] / prod_monthly["ATR_PROD"] * 100.0,
            np.nan,
        )

        gap_hist = contract_monthly[["MONTH", "CONTRACT_RATE_PCT"]].merge(
            prod_monthly[["MONTH", "PORT_ACTUAL_PCT"]], on="MONTH", how="inner"
        )
        gap_hist["GAP_PP"] = gap_hist["CONTRACT_RATE_PCT"] - gap_hist["PORT_ACTUAL_PCT"]
        gap_hist = gap_hist.dropna(subset=["MONTH", "GAP_PP"]).sort_values("MONTH")

        # Global fallback mirrors app-style default behavior if needed.
        recent12 = gap_hist.tail(12)["GAP_PP"]
        global_fallback = float(recent12.mean()) if len(recent12) >= 4 else 1.6

        eval_df = bt.dropna(subset=["MONTH", "PORT_PRED_PCT", "ACTUAL_PCT"]).copy()
        eval_df = eval_df[eval_df["MONTH"] < pd.Timestamp.today().normalize().replace(day=1)].sort_values("MONTH")

        methods = [
            "flat_1p6",
            "trailing12_mean",
            "current_app_style_trailing3",
            "finance6_trim",
            "finance6_median",
            "finance6_ewma_h3",
            "winsor12_then_recent6",
            "adaptive_recent_plus_longrun",
            "blended_6_trim_plus_seasonal",
            "seasonal_shrink_24",
            "huber_recent6",
            "robust_ewma_winsor",
        ]

        rows = []
        by_month_rows = []

        for method in methods:
            preds = []
            actuals = []
            months = []

            for _, r in eval_df.iterrows():
                m = pd.Timestamp(r["MONTH"])
                net_pp = estimate_netting(method, m, gap_hist, global_fallback)
                pred = float(r["PORT_PRED_PCT"]) + net_pp
                act = float(r["ACTUAL_PCT"])

                preds.append(pred)
                actuals.append(act)
                months.append(m)
                by_month_rows.append(
                    {
                        "MONTH": m,
                        "METHOD": method,
                        "ATR": float(r["ATR"]),
                        "PORT_PRED_PCT": float(r["PORT_PRED_PCT"]),
                        "NETTING_PP": net_pp,
                        "BOARD_PRED_PCT": pred,
                        "ACTUAL_PCT": act,
                        "ERROR_PP": pred - act,
                    }
                )

            err = np.array(preds) - np.array(actuals)
            mae = float(np.mean(np.abs(err)))
            rmse = float(np.sqrt(np.mean(np.square(err))))
            bias = float(np.mean(err))

            atr_w = eval_df["ATR"].to_numpy(dtype=float)
            atr_w = np.where(np.isfinite(atr_w) & (atr_w > 0), atr_w, 0.0)
            if atr_w.sum() > 0:
                wmae = float(np.sum(np.abs(err) * atr_w) / np.sum(atr_w))
                wrmse = float(np.sqrt(np.sum(np.square(err) * atr_w) / np.sum(atr_w)))
            else:
                wmae = mae
                wrmse = rmse

            recent_cut = eval_df["MONTH"].max() - pd.DateOffset(months=11)
            recent_mask = eval_df["MONTH"] >= recent_cut
            recent_err = err[recent_mask.to_numpy()]
            recent_mae = float(np.mean(np.abs(recent_err))) if len(recent_err) else np.nan
            recent_bias = float(np.mean(recent_err)) if len(recent_err) else np.nan

            rows.append(
                {
                    "method": method,
                    "n_months": len(err),
                    "mae_pp": mae,
                    "wmae_pp": wmae,
                    "rmse_pp": rmse,
                    "wrmse_pp": wrmse,
                    "bias_pp": bias,
                    "recent12_mae_pp": recent_mae,
                    "recent12_bias_pp": recent_bias,
                }
            )

        # Native contract model baseline from backtest table.
        native = eval_df.dropna(subset=["CONTRACT_PRED_PCT"]).copy()
        if not native.empty:
            n_err = native["CONTRACT_PRED_PCT"].to_numpy() - native["ACTUAL_PCT"].to_numpy()
            atr_w_n = native["ATR"].to_numpy(dtype=float)
            atr_w_n = np.where(np.isfinite(atr_w_n) & (atr_w_n > 0), atr_w_n, 0.0)
            n_wmae = float(np.sum(np.abs(n_err) * atr_w_n) / np.sum(atr_w_n)) if atr_w_n.sum() > 0 else float(np.mean(np.abs(n_err)))
            n_wrmse = float(np.sqrt(np.sum(np.square(n_err) * atr_w_n) / np.sum(atr_w_n))) if atr_w_n.sum() > 0 else float(np.sqrt(np.mean(np.square(n_err))))
            n_recent_cut = native["MONTH"].max() - pd.DateOffset(months=11)
            n_recent = n_err[(native["MONTH"] >= n_recent_cut).to_numpy()]
            rows.append(
                {
                    "method": "native_contract_model_baseline",
                    "n_months": int(len(n_err)),
                    "mae_pp": float(np.mean(np.abs(n_err))),
                    "wmae_pp": n_wmae,
                    "rmse_pp": float(np.sqrt(np.mean(np.square(n_err)))),
                    "wrmse_pp": n_wrmse,
                    "bias_pp": float(np.mean(n_err)),
                    "recent12_mae_pp": float(np.mean(np.abs(n_recent))) if len(n_recent) else np.nan,
                    "recent12_bias_pp": float(np.mean(n_recent)) if len(n_recent) else np.nan,
                }
            )

        out = pd.DataFrame(rows)
        out_netting = out[out["method"] != "native_contract_model_baseline"].copy()
        # Composite ranking for netting challengers only.
        out_netting["rank_mae"] = out_netting["mae_pp"].rank(method="min")
        out_netting["rank_wmae"] = out_netting["wmae_pp"].rank(method="min")
        out_netting["rank_rmse"] = out_netting["rmse_pp"].rank(method="min")
        out_netting["rank_recent"] = out_netting["recent12_mae_pp"].rank(method="min")
        out_netting["rank_abs_bias"] = out_netting["bias_pp"].abs().rank(method="min")
        out_netting["composite_rank_score"] = (
            out_netting["rank_mae"]
            + out_netting["rank_wmae"]
            + 0.75 * out_netting["rank_rmse"]
            + 0.75 * out_netting["rank_recent"]
            + 0.5 * out_netting["rank_abs_bias"]
        )
        out_netting = out_netting.sort_values("composite_rank_score").reset_index(drop=True)

        out = out.sort_values("mae_pp").reset_index(drop=True)
        print("\n=== Netting Method Walk-Forward Accuracy (lower is better) ===")
        print(out.to_string(index=False, float_format=lambda x: f"{x:0.4f}"))

        print("\n=== Netting Challengers Composite Ranking (lower is better) ===")
        print(
            out_netting[
                [
                    "method",
                    "composite_rank_score",
                    "mae_pp",
                    "wmae_pp",
                    "rmse_pp",
                    "recent12_mae_pp",
                    "bias_pp",
                ]
            ].to_string(index=False, float_format=lambda x: f"{x:0.4f}")
        )

        if len(out) >= 2:
            best = out.iloc[0]
            second = out.iloc[1]
            print("\nBest method:")
            print(
                f"  {best['method']} | MAE={best['mae_pp']:.3f}pp RMSE={best['rmse_pp']:.3f}pp Bias={best['bias_pp']:+.3f}pp"
            )
            print(
                f"  Improvement vs next best (MAE): {second['mae_pp'] - best['mae_pp']:+.3f}pp"
            )

        # Save detailed month-level diagnostics for audit/review.
        month_diag = pd.DataFrame(by_month_rows).sort_values(["MONTH", "METHOD"])
        out_file = Path(__file__).with_name("netting_method_walkforward_results.csv")
        month_diag.to_csv(out_file, index=False)
        print(f"\nSaved month-level diagnostics: {out_file}")

        out_file_summary = Path(__file__).with_name("netting_method_summary_results.csv")
        out.to_csv(out_file_summary, index=False)
        out_file_rank = Path(__file__).with_name("netting_method_composite_rank.csv")
        out_netting.to_csv(out_file_rank, index=False)
        print(f"Saved method summary: {out_file_summary}")
        print(f"Saved composite rank: {out_file_rank}")

    finally:
        conn.close()


if __name__ == "__main__":
    evaluate()
