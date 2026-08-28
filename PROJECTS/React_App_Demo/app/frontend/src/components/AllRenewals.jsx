import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts"
import { fmt$, fmtPct, fmtN, pctClass, errClass, fmtMonth } from "./formatters.js"

function KpiCard({ label, value, delta, deltaClass }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {delta !== undefined && (
        <div className={`kpi-delta ${deltaClass ?? "neutral"}`}>{delta}</div>
      )}
    </div>
  )
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: "var(--bg-2)", border: "1px solid var(--line)",
      borderRadius: 10, padding: "10px 14px", fontSize: 12,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6, color: "var(--text-0)" }}>{label}</div>
      {payload.map(p => (
        <div key={p.name} style={{ color: p.color, marginBottom: 3 }}>
          {p.name}: {p.name.includes("%") ? fmtPct(p.value) : fmt$(p.value)}
        </div>
      ))}
    </div>
  )
}

export default function AllRenewals({ rows, segmentRows }) {
  if (!rows.length) return <div className="state-msg">No data for this date range.</div>

  const totalATR      = rows.reduce((s, r) => s + (Number(r.atr) || 0), 0)
  const totalForecast = rows.reduce((s, r) => s + (Number(r.effective_forecast) || 0), 0)
  const totalActual   = rows.reduce((s, r) => s + (Number(r.actual_retained) || 0), 0)
  const totalAtRisk   = rows.reduce((s, r) => s + (Number(r.at_risk_dollars) || 0), 0)
  const forecastPct   = totalATR > 0 ? (totalForecast / totalATR) * 100 : 0
  const actualPct     = totalATR > 0 ? (totalActual / totalATR) * 100 : 0

  // Renewal rate % chart
  const trendData = rows.map(r => ({
    month:       fmtMonth(r.renewal_month),
    "Forecast %":  Number(r.forecast_pct) || 0,
    "Actual %":    Number(r.actual_pct) || 0,
    "ML %":        Number(r.ml_forecast_pct) || 0,
    matured:     r.is_matured_month,
  }))

  // ATR chart
  const atrData = rows.map(r => ({
    month:    fmtMonth(r.renewal_month),
    ATR:      Number(r.atr) || 0,
    Forecast: Number(r.effective_forecast) || 0,
    Actual:   Number(r.actual_retained) || 0,
  }))

  return (
    <div>
      {/* KPI row */}
      <div className="kpi-grid">
        <KpiCard label="Total ATR"          value={fmt$(totalATR)} />
        <KpiCard label="Renewal Forecast"   value={fmt$(totalForecast)}
          delta={fmtPct(forecastPct)} deltaClass={pctClass(forecastPct)} />
        <KpiCard label="Actual Retained"    value={fmt$(totalActual)}
          delta={fmtPct(actualPct)} deltaClass={pctClass(actualPct)} />
        <KpiCard label="At-Risk $"          value={fmt$(totalAtRisk)} deltaClass="neg" />
      </div>

      {/* Renewal Rate % trend */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Renewal Rate % by Month — All Cohorts</span>
        </div>
        <div className="card-body">
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={trendData} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="month" tick={{ fill: "var(--text-2)", fontSize: 11 }} tickLine={false} />
              <YAxis domain={[0, 100]} tickFormatter={v => `${v}%`} tick={{ fill: "var(--text-2)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip content={<ChartTooltip />} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="Forecast %" stroke="var(--green)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--green)", strokeWidth: 0 }} />
              <Line dataKey="Actual %"   stroke="var(--accent)" strokeWidth={2} dot={{ r: 3, fill: "var(--accent)", strokeWidth: 0 }} />
              <Line dataKey="ML %"       stroke="var(--gold)" strokeWidth={1.5} dot={false} strokeDasharray="4 3" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Monthly rollup table */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Monthly Rollup — All Renewals</span>
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Month</th>
                <th className="num">Contracts</th>
                <th className="num">ATR</th>
                <th className="num">Renewal Forecast</th>
                <th className="num">Actual Retained</th>
                <th className="num">Forecast %</th>
                <th className="num">Actual %</th>
                <th className="num">At-Risk $</th>
                <th>Matured</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{fmtMonth(r.renewal_month)}</td>
                  <td className="num">{fmtN(r.contracts)}</td>
                  <td className="num">{fmt$(r.atr)}</td>
                  <td className="num">{fmt$(r.effective_forecast)}</td>
                  <td className="num">{fmt$(r.actual_retained)}</td>
                  <td className={`num ${pctClass(r.forecast_pct)}`}>{fmtPct(r.forecast_pct)}</td>
                  <td className={`num ${pctClass(r.actual_pct)}`}>{fmtPct(r.actual_pct)}</td>
                  <td className="num clr-neg">{fmt$(r.at_risk_dollars)}</td>
                  <td>{r.is_matured_month ? <span className="badge badge-pass">Yes</span> : <span style={{color:"var(--text-2)", fontSize:11}}>Open</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Segment rollup */}
      {segmentRows && segmentRows.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">By Segment</span>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Segment</th>
                  <th className="num">Contracts</th>
                  <th className="num">ATR</th>
                  <th className="num">Renewal Forecast</th>
                  <th className="num">Actual Retained</th>
                  <th className="num">Forecast %</th>
                  <th className="num">Actual %</th>
                  <th className="num">At-Risk $</th>
                  <th className="num">Avg Churn %</th>
                </tr>
              </thead>
              <tbody>
                {segmentRows.map((r, i) => (
                  <tr key={i}>
                    <td><strong style={{ color: "var(--text-0)" }}>{r.segment ?? "—"}</strong></td>
                    <td className="num">{fmtN(r.contracts)}</td>
                    <td className="num">{fmt$(r.atr)}</td>
                    <td className="num">{fmt$(r.effective_forecast)}</td>
                    <td className="num">{fmt$(r.actual_retained)}</td>
                    <td className={`num ${pctClass(r.forecast_pct)}`}>{fmtPct(r.forecast_pct)}</td>
                    <td className={`num ${pctClass(r.actual_pct)}`}>{fmtPct(r.actual_pct)}</td>
                    <td className="num clr-neg">{fmt$(r.at_risk_dollars)}</td>
                    <td className={`num ${errClass(r.avg_churn_pct)}`}>{fmtPct(r.avg_churn_pct)}</td>
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
