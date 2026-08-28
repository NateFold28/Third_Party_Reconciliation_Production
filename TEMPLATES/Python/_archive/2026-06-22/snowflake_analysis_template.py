import pandas as pd
import matplotlib.pyplot as plt

from connection import fetch_dataframe

QUERY = """
SELECT *
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_BEHAVIOR_CLUSTERS
LIMIT 1000
"""


def run_analysis_loop(df: pd.DataFrame) -> None:
    print(f"Rows: {len(df):,}")
    print(f"Columns: {len(df.columns)}")
    print("\nDtypes:")
    print(df.dtypes)

    print("\nNull rates (%):")
    null_rates = (df.isna().mean() * 100).sort_values(ascending=False)
    print(null_rates.head(15).round(2))

    numeric_cols = df.select_dtypes(include="number").columns
    if len(numeric_cols) > 0:
        summary = df[numeric_cols].describe().T
        print("\nNumeric summary:")
        print(summary)

        col = numeric_cols[0]
        plt.figure(figsize=(10, 5))
        df[col].dropna().hist(bins=30)
        plt.title(f"Distribution: {col}")
        plt.xlabel(col)
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig("analysis_histogram.png")
        print("\nSaved chart: analysis_histogram.png")


def main() -> None:
    df = fetch_dataframe(QUERY)
    run_analysis_loop(df)


if __name__ == "__main__":
    main()
