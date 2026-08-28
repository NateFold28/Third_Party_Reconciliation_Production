"""Side-by-side comparison: challenger netting vs current app netting.

Inputs:
- TEMPLATES/Python/netting_method_walkforward_results.csv

Outputs:
- Console summary (MAE/RMSE/Bias, paired deltas)
- CSV: TEMPLATES/Python/netting_challenger_vs_current_summary.csv
- CSV: TEMPLATES/Python/netting_challenger_vs_current_by_month.csv
"""

from __future__ import annotations

from math import comb, sqrt
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(__file__).resolve().parent
IN_FILE = BASE / "netting_method_walkforward_results.csv"
OUT_SUM = BASE / "netting_challenger_vs_current_summary.csv"
OUT_BYM = BASE / "netting_challenger_vs_current_by_month.csv"

CURRENT = "current_app_style_trailing3"
CHALLENGER = "adaptive_recent_plus_longrun"


def two_sided_sign_test_pvalue(n_pos: int, n_non_tie: int) -> float:
    """Exact two-sided sign-test p-value under H0: p=0.5."""
    if n_non_tie <= 0:
        return 1.0
    k = min(n_pos, n_non_tie - n_pos)
    p = sum(comb(n_non_tie, i) for i in range(0, k + 1)) / (2 ** n_non_tie)
    return min(1.0, 2.0 * p)


def metrics(err: np.ndarray) -> tuple[float, float, float]:
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(np.square(err))))
    bias = float(np.mean(err))
    return mae, rmse, bias


def main() -> None:
    if not IN_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {IN_FILE}")

    df = pd.read_csv(IN_FILE)
    df["MONTH"] = pd.to_datetime(df["MONTH"], errors="coerce")

    need_cols = {"MONTH", "METHOD", "ERROR_PP", "BOARD_PRED_PCT", "ACTUAL_PCT", "NETTING_PP"}
    missing = need_cols - set(df.columns)
    if missing:
        raise ValueError(f"Input missing required columns: {sorted(missing)}")

    cur = df[df["METHOD"] == CURRENT][["MONTH", "ERROR_PP", "BOARD_PRED_PCT", "ACTUAL_PCT", "NETTING_PP"]].rename(
        columns={
            "ERROR_PP": "ERROR_CURRENT",
            "BOARD_PRED_PCT": "PRED_CURRENT",
            "NETTING_PP": "NETTING_CURRENT",
        }
    )
    ch = df[df["METHOD"] == CHALLENGER][["MONTH", "ERROR_PP", "BOARD_PRED_PCT", "ACTUAL_PCT", "NETTING_PP"]].rename(
        columns={
            "ERROR_PP": "ERROR_CHALLENGER",
            "BOARD_PRED_PCT": "PRED_CHALLENGER",
            "NETTING_PP": "NETTING_CHALLENGER",
        }
    )

    merged = cur.merge(ch, on=["MONTH", "ACTUAL_PCT"], how="inner").sort_values("MONTH")
    if merged.empty:
        raise ValueError("No overlapping months between current and challenger methods")

    merged["ABS_ERR_CURRENT"] = merged["ERROR_CURRENT"].abs()
    merged["ABS_ERR_CHALLENGER"] = merged["ERROR_CHALLENGER"].abs()
    merged["ABS_ERR_DELTA"] = merged["ABS_ERR_CHALLENGER"] - merged["ABS_ERR_CURRENT"]
    merged["ERR_DELTA"] = merged["ERROR_CHALLENGER"] - merged["ERROR_CURRENT"]
    merged["NETTING_DELTA"] = merged["NETTING_CHALLENGER"] - merged["NETTING_CURRENT"]

    e_cur = merged["ERROR_CURRENT"].to_numpy(dtype=float)
    e_ch = merged["ERROR_CHALLENGER"].to_numpy(dtype=float)

    mae_cur, rmse_cur, bias_cur = metrics(e_cur)
    mae_ch, rmse_ch, bias_ch = metrics(e_ch)

    mae_gain_pp = mae_cur - mae_ch
    rmse_gain_pp = rmse_cur - rmse_ch
    bias_abs_gain_pp = abs(bias_cur) - abs(bias_ch)

    improved_months = int((merged["ABS_ERR_DELTA"] < 0).sum())
    worsened_months = int((merged["ABS_ERR_DELTA"] > 0).sum())
    ties = int((merged["ABS_ERR_DELTA"] == 0).sum())
    n_non_tie = improved_months + worsened_months
    sign_p = two_sided_sign_test_pvalue(improved_months, n_non_tie)

    # Simple paired effect-size style summary.
    abs_delta = merged["ABS_ERR_DELTA"].to_numpy(dtype=float)
    mean_delta = float(np.mean(abs_delta))  # challenger - current, lower is better
    sd_delta = float(np.std(abs_delta, ddof=1)) if len(abs_delta) > 1 else 0.0
    se_delta = sd_delta / sqrt(len(abs_delta)) if len(abs_delta) > 0 else float("nan")
    ci_lo = mean_delta - 1.96 * se_delta if np.isfinite(se_delta) else float("nan")
    ci_hi = mean_delta + 1.96 * se_delta if np.isfinite(se_delta) else float("nan")

    summary = pd.DataFrame(
        [
            {
                "months_compared": len(merged),
                "current_method": CURRENT,
                "challenger_method": CHALLENGER,
                "mae_current_pp": mae_cur,
                "mae_challenger_pp": mae_ch,
                "mae_gain_pp": mae_gain_pp,
                "mae_gain_pct": (mae_gain_pp / mae_cur * 100.0) if mae_cur else np.nan,
                "rmse_current_pp": rmse_cur,
                "rmse_challenger_pp": rmse_ch,
                "rmse_gain_pp": rmse_gain_pp,
                "bias_current_pp": bias_cur,
                "bias_challenger_pp": bias_ch,
                "bias_abs_gain_pp": bias_abs_gain_pp,
                "improved_months": improved_months,
                "worsened_months": worsened_months,
                "tie_months": ties,
                "sign_test_pvalue": sign_p,
                "mean_abs_err_delta_pp": mean_delta,
                "mean_abs_err_delta_ci95_lo": ci_lo,
                "mean_abs_err_delta_ci95_hi": ci_hi,
            }
        ]
    )

    summary.to_csv(OUT_SUM, index=False)
    merged.to_csv(OUT_BYM, index=False)

    row = summary.iloc[0]
    print("\n=== Challenger vs Current Netting (paired by month) ===")
    print(f"Months compared: {int(row['months_compared'])}")
    print(f"Current:    {CURRENT}")
    print(f"Challenger: {CHALLENGER}")
    print("\nAccuracy:")
    print(f"  MAE   current={row['mae_current_pp']:.4f}  challenger={row['mae_challenger_pp']:.4f}  gain={row['mae_gain_pp']:+.4f} pp ({row['mae_gain_pct']:+.2f}%)")
    print(f"  RMSE  current={row['rmse_current_pp']:.4f} challenger={row['rmse_challenger_pp']:.4f} gain={row['rmse_gain_pp']:+.4f} pp")
    print(f"  |Bias| current={abs(row['bias_current_pp']):.4f} challenger={abs(row['bias_challenger_pp']):.4f} gain={row['bias_abs_gain_pp']:+.4f} pp")
    print("\nPaired month-level consistency:")
    print(f"  Improved months: {int(row['improved_months'])}")
    print(f"  Worsened months: {int(row['worsened_months'])}")
    print(f"  Ties:            {int(row['tie_months'])}")
    print(f"  Sign-test p-val: {row['sign_test_pvalue']:.4f}")
    print(f"  Mean abs-error delta (challenger-current): {row['mean_abs_err_delta_pp']:+.4f} pp")
    print(f"  95% CI: [{row['mean_abs_err_delta_ci95_lo']:+.4f}, {row['mean_abs_err_delta_ci95_hi']:+.4f}] pp")
    print("\nWrote:")
    print(f"  {OUT_SUM}")
    print(f"  {OUT_BYM}")


if __name__ == "__main__":
    main()
