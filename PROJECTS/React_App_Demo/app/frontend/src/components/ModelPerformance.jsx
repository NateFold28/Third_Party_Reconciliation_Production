import {
  LineChart, Line, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, ReferenceLine,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts"
import { fmt$, fmtPct, fmtN, pctClass, errClass, fmtMonth } from "./formatters.js"

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: "var(--bg-2)", border: "1px solid var(--line)",
      borderRadius: 10, padding: "10px 14px", fontSize: 12,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6, color: "var(--text-0)" }}>{label}</div>
      {payload.map(p => (
        <div key={p.dataKey} style={{ color: p.color, marginBottom: 3 }}>
          {p.name}: {p.name.includes("Rate") ? fmtPct(p.value) : p.value?.toFixed ? fmtPct(p.value) : p.value}
        </div>
      ))}
    </div>
  )
}

export default function ModelPerformance({ rows, runs }) {
  if (!rows.length && !runs?.length) return <div className="state-msg">No model performance data for this date range.</div>

  // Aggregate backtest metrics (all segments combined)
  const byMonth = {}
  rows.forEach(r => {
    const mo = fmtMonth(r.renewal_month)
    if (!byMonth[mo]) byMonth[mo] = { month: mo, atr: 0, predicted: 0, actual: 0, n: 0 }
    byMonth[mo].atr       += Number(r.atr) || 0
    byMonth[mo].predicted += Number(r.predicted_retained) || 0
    byMonth[mo].actual    += Number(r.actual_retained) || 0
    byMonth[mo].n         += Number(r.n_contracts) || 0
  })
  const rollup = Object.values(byMonth).map(m => ({
    month:           m.month,
    "Predicted Rate": m.atr > 0 ? (m.predicted / m.atr) * 100 : 0,
    "Actual Rate":    m.atr > 0 ? (m.actual / m.atr) * 100 : 0,
    error_pp:        m.atr > 0 ? ((m.predicted - m.actual) / m.atr) * 100 : 0,
  }))

  // Champion run
  const champion = runs?.find(r => r.is_champion)

  // Gate status
  const gatePass  = champion?.champion_gate_passed
  const gateLabel = gatePass === true ? "PASS" : gatePass === false ? "FAIL" : "—"
  const gateClass = gatePass === true ? "badge-pass" : gatePass === false ? "badge-fail" : ""

  // Overall MAE
  const errs = rows.filter(r => r.actual_rate_pct > 0 && r.predicted_rate_pct > 0)
  const mae  = errs.length
    ? errs.reduce((s, r) => s + Math.abs(Number(r.error_pp) || 0), 0) / errs.length
    : null

  return (
    <div>
      {/* Champion info banner */}
      {champion && (
        <div className="info-banner" style={{ marginBottom: 20 }}>
          <strong>Champion Run:</strong> {champion.run_id} · Method: {champion.method} ·{" "}
          Contracts: {fmtN(champion.n_contracts)} ·{" "}
          Forecast Rate: {fmtPct(champion.forecast_rate_pct)}{" "}
          {gateLabel !== "—" && (
            <span className={`badge ${gateClass}`} style={{ marginLeft: 8 }}>{gateLabel}</span>
          )}
        </div>
      )}

      {/* KPI row */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">Months Backtested</div>
          <div className="kpi-value">{rollup.length}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Backtest MAE (pp)</div>
          <div className="kpi-value">{mae !== null ? `±${mae.toFixed(1)}pp` : "—"}</div>
          <div className={`kpi-delta ${mae !== null && mae <= 5 ? "pos" : "neg"}`}>
            {mae !== null && mae <= 5 ? "Within Gate" : mae !== null ? "Exceeds Gate" : ""}
          </div>
        </div>
        {champion && (
          <>
            <div className="kpi-card">
              <div className="kpi-label">Champion Method</div>
              <div className="kpi-value" style={{ fontSize: 14 }}>{champion.method}</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Champion Gate</div>
              <div className="kpi-value">
                <span className={`badge ${gateClass}`}>{gateLabel}</span>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Backtest accuracy chart */}
      {rollup.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Backtest: Predicted vs Actual Renewal Rate %</span>
          </div>
          <div className="card-body">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={rollup} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                <XAxis dataKey="month" tick={{ fill: "var(--text-2)", fontSize: 11 }} tickLine={false} />
                <YAxis domain={[0, 100]} tickFormatter={v => `${v}%`} tick={{ fill: "var(--text-2)", fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<ChartTooltip />} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey="Predicted Rate" stroke="var(--gold)" strokeWidth={2} dot={{ r: 3, fill: "var(--gold)", strokeWidth: 0 }} />
                <Line dataKey="Actual Rate"    stroke="var(--green)" strokeWidth={2} dot={{ r: 3, fill: "var(--green)", strokeWidth: 0 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Backtest by segment table */}
      {rows.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Backtest Detail — By Segment × Month</span>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Month</th>
                  <th>Segment</th>
                  <th className="num">Contracts</th>
                  <th className="num">ATR</th>
                  <th className="num">Predicted Rate</th>
                  <th className="num">Actual Rate</th>
                  <th className="num">Error (pp)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{fmtMonth(r.renewal_month)}</td>
                    <td>{r.segment ?? "—"}</td>
                    <td className="num">{fmtN(r.n_contracts)}</td>
                    <td className="num">{fmt$(r.atr)}</td>
                    <td className={`num ${pctClass(r.predicted_rate_pct)}`}>{fmtPct(r.predicted_rate_pct)}</td>
                    <td className={`num ${pctClass(r.actual_rate_pct)}`}>{fmtPct(r.actual_rate_pct)}</td>
                    <td className={`num ${errClass(r.error_pp)}`}>{r.error_pp > 0 ? "+" : ""}{fmtPct(r.error_pp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Model runs table */}
      {runs && runs.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Recent Model Runs</span>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run Date</th>
                  <th>Method</th>
                  <th className="num">Contracts</th>
                  <th className="num">Forecast Rate</th>
                  <th>Champion</th>
                  <th>Gate</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r, i) => (
                  <tr key={i}>
                    <td>{r.run_timestamp ?? "—"}</td>
                    <td style={{ fontFamily: "monospace", fontSize: 11 }}>{r.method}</td>
                    <td className="num">{fmtN(r.n_contracts)}</td>
                    <td className={`num ${pctClass(r.forecast_rate_pct)}`}>{fmtPct(r.forecast_rate_pct)}</td>
                    <td>
                      {r.is_champion
                        ? <span className="badge badge-champ">Champion</span>
                        : <span style={{ color: "var(--text-2)", fontSize: 11 }}>—</span>
                      }
                    </td>
                    <td>
                      {r.champion_gate_passed === true  && <span className="badge badge-pass">PASS</span>}
                      {r.champion_gate_passed === false && <span className="badge badge-fail">FAIL</span>}
                      {r.champion_gate_passed === null  && <span style={{ color: "var(--text-2)", fontSize: 11 }}>—</span>}
                    </td>
                    <td style={{ color: "var(--text-2)", fontSize: 11, maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.notes ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
