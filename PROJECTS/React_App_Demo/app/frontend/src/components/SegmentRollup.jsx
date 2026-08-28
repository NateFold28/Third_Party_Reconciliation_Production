import { fmt$, fmtPct, pctClass } from './formatters.js'

export default function SegmentRollup({ rows }) {
  if (!rows.length) return <div className="state-msg">No data for this date range.</div>

  return (
    <div style={{ background: '#fff', borderRadius: 8, border: '1px solid #e2e4ea', overflow: 'hidden' }}>
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th className="num">ATR</th>
            <th className="num">Actuals</th>
            <th className="num">Actual %</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.segment ?? '—'}</td>
              <td className="num">{fmt$(r.atr)}</td>
              <td className="num">{fmt$(r.actuals)}</td>
              <td className={`num ${pctClass(r.actual_pct)}`}>{fmtPct(r.actual_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
