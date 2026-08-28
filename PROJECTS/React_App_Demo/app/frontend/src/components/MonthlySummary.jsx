import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { fmt$, fmtPct, pctClass } from './formatters.js'

export default function MonthlySummary({ rows }) {
  if (!rows.length) return <div className="state-msg">No data for this date range.</div>

  const chartData = rows.map(r => ({
    month:  r.renewal_month?.slice(0, 7) ?? '',
    ATR:    Number(r.atr)    || 0,
    Actuals: Number(r.actuals) || 0,
  }))

  return (
    <div>
      {/* Chart */}
      <div style={{ background: '#fff', borderRadius: 8, border: '1px solid #e2e4ea', padding: '20px 16px', marginBottom: 24 }}>
        <h2 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>ATR vs Actuals — Monthly</h2>
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chartData} margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e4ea" />
            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
            <YAxis
              tickFormatter={v => `$${(v / 1_000_000).toFixed(1)}M`}
              tick={{ fontSize: 11 }}
            />
            <Tooltip formatter={(v, n) => [`$${(v / 1_000_000).toFixed(2)}M`, n]} />
            <Legend />
            <Bar  dataKey="ATR"     fill="#0068d9" radius={[3, 3, 0, 0]} />
            <Line dataKey="Actuals" stroke="#16a34a" strokeWidth={2} dot={{ r: 3 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Table */}
      <div style={{ background: '#fff', borderRadius: 8, border: '1px solid #e2e4ea', overflow: 'hidden' }}>
        <table>
          <thead>
            <tr>
              <th>Month</th>
              <th className="num">ATR</th>
              <th className="num">Actuals</th>
              <th className="num">Actual %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>{r.renewal_month?.slice(0, 7)}</td>
                <td className="num">{fmt$(r.atr)}</td>
                <td className="num">{fmt$(r.actuals)}</td>
                <td className={`num ${pctClass(r.actual_pct)}`}>{fmtPct(r.actual_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
