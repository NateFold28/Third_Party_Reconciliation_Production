"""Deploy isotonic calibration knots to STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS."""
import sys
from pathlib import Path
import json
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connection import fetch_dataframe, execute_sql, get_snowflake_connection
import pandas as pd

KNOT_TABLE = "STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS"

with open(_HERE / "isotonic_calibrators_per_horizon.json") as f:
    raw = json.load(f)

rows = []
for target, seg_h_map in raw.items():
    for key, pts in seg_h_map.items():
        seg, h = key.rsplit("|", 1)
        rows.append({
            "MODEL_TARGET": target,
            "SEGMENT":      seg,
            "HORIZON":      int(h),
            "KNOT_X_JSON":  json.dumps(pts["x"]),
            "KNOT_Y_JSON":  json.dumps(pts["y"]),
            "N_KNOTS":      len(pts["x"]),
            "DESCRIPTION":  (
                "Isotonic calibration | " + target +
                " | Seg=" + seg + " | H=" + h +
                " | Trained Dec-2025..Feb-2026 | Validated Mar-2026..May-2026"
            ),
        })
df_rows = pd.DataFrame(rows)
print("Total rows:", len(df_rows))

conn = get_snowflake_connection()

print("Recreating", KNOT_TABLE)
execute_sql("DROP TABLE IF EXISTS " + KNOT_TABLE, conn=conn)
execute_sql(
    "CREATE TABLE " + KNOT_TABLE + " ("
    "    MODEL_TARGET   VARCHAR(64)   NOT NULL,"
    "    SEGMENT        VARCHAR(64)   NOT NULL,"
    "    HORIZON        INTEGER       NOT NULL,"
    "    KNOT_X_JSON    VARCHAR(8192) NOT NULL,"
    "    KNOT_Y_JSON    VARCHAR(8192) NOT NULL,"
    "    N_KNOTS        INTEGER,"
    "    DESCRIPTION    VARCHAR(512),"
    "    INSERTED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()"
    ")",
    conn=conn
)

conn.cursor().execute("USE DATABASE STREAMLIT_APPS")
conn.cursor().execute("USE SCHEMA DBO")

insert_sql = (
    "INSERT INTO V5_CALIBRATION_KNOTS "
    "(MODEL_TARGET, SEGMENT, HORIZON, KNOT_X_JSON, KNOT_Y_JSON, N_KNOTS, DESCRIPTION) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
param_rows = [
    (
        r["MODEL_TARGET"],
        r["SEGMENT"],
        int(r["HORIZON"]),
        r["KNOT_X_JSON"],
        r["KNOT_Y_JSON"],
        int(r["N_KNOTS"]),
        r["DESCRIPTION"],
    )
    for _, r in df_rows.iterrows()
]

cur = conn.cursor()
cur.executemany(insert_sql, param_rows)
print("Inserted rows:", len(param_rows))

verify = fetch_dataframe(
    "SELECT MODEL_TARGET, SEGMENT, HORIZON, N_KNOTS FROM " + KNOT_TABLE +
    " ORDER BY MODEL_TARGET, SEGMENT, HORIZON",
    conn=conn
)
print("Verification:", len(verify), "rows in Snowflake:")
print(verify.to_string(index=False))
print("Calibration knots deployed successfully.")
