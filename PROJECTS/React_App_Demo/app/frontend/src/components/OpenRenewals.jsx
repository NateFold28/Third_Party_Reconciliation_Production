import {
  ComposedChart, Bar, Line,
  XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts"
import { fmt$, fmtPct, fmtN, pctClass, fmtMonth } from "./formatters.js"

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
          {p.name}: {fmt$(p.value)}
        </div>
      ))}
    </div>
  )
}

export default function OpenRenewals({ rows }) {
  if (!rows.length) return <div className="state-msg">No open renewals in this date range.</div>

  const totalATR       = rows.reduce((s, r) => s + (Number(r.atr) || 0), 0)
  const totalForecast  = rows.reduce((s, r) => s + (Number(r.effective_forecast) || 0), 0)
  const totalML        = rows.reduce((s, r) => s + (Number(r.ml_forecast) || 0), 0)
  const totalAtRisk    = rows.reduce((s, r) => s + (Number(r.at_risk_dollars) || 0), 0)
  const totalContracts = rows.reduce((s, r) => s + (Number(r.contracts) || 0), 0)
  const forecastPct    = totalATR > 0 ? (totalForecast / totalATR) * 100 : 0
  const mlPct          = totalATR > 0 ? (totalML / totalATR) * 100 : 0

  const chartData = rows.map(r => ({
    month:    fmtMonth(r.renewal_month),
    ATR:      Number(r.atr) || 0,
    Forecast: Number(r.effective_forecast) || 0,
    ML:       Number(r.ml_forecast) || 0,
  }))

  return (
    <div>
      <div className="kpi-grid">
        <KpiCard label="Total ATR"        value={fmt$(totalATR)} />
        <KpiCard label="Renewal Forecast" value={fmt$(totalForecast)}
          delta={fmtPct(forecastPct)} deltaClass={pctClass(forecastPct)} />
        <KpiCard label="ML Forecast"      value={fmt$(totalML)}
          delta={fmtPct(mlPct)} deltaClass={pctClass(mlPct)} />
        <KpiCard label="At-Risk $"        value={fmt$(totalAtRisk)} deltaClass="neg" />
        <KpiCard label="Contracts"        value={fmtN(totalContracts)} />
      </div>

      <div className="card">
        <div className="card-header">
          <span className="card-title">ATR vs Renewal Forecast by Month — Open Cohort</span>
        </div>
        <div className="card-body">
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="month" tick={{ fill: "var(--text-2)", fontSize: 11 }} tickLine={false} />
              <YAxis tickFormatter={v => fmt$(v)} tick={{ fill: "var(--text-2)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip content={<ChartTooltip />} />
              <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-2)" }} />
              <Bar dataKey="ATR" fill="rgba(56,189,248,0.30)" stroke="var(--accent)" strokeWidth={1} radius={[4,4,0,0]} />
              <Line dataKey="Forecast" stroke="var(--green)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--green)", strokeWidth: 0 }} name="Renewal Forecast" />
              <Line dataKey="ML" stroke="var(--gold)" strokeWidth={1.5} dot={false} strokeDasharray="4 3" name="ML Forecast" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <span className="card-title">Monthly Rollup — Open Renewals</span>
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Month</th>
                <th className="num">Contracts</th>
                <th className="num">ATR</th>
                <th className="num">ML Forecast</th>
                <th className="num">Renewal Forecast</th>
                <th className="num">Forecast %</th>
                <th className="num">At-Risk $</th>
                <th className="num">Open Opp</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{fmtMonth(r.renewal_month)}</td>
                  <td className="num">{fmtN(r.contracts)}</td>
                  <td className="num">{fmt$(r.atr)}</td>
                  <td className="num">{fmt$(r.ml_forecast)}</td>
                  <td className="num">{fmt$(r.effective_forecast)}</td>
                  <td className={`num ${pctClass(r.forecast_pct)}`}>{fmtPct(r.forecast_pct)}</td>
                  <td className="num clr-neg">{fmt$(r.at_risk_dollars)}</td>
                  <td className="num">{fmt$(r.open_opp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
