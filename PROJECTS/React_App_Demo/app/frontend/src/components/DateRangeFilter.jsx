export default function DateRangeFilter({ startDate, endDate, onStartChange, onEndChange }) {
  return (
    <div className="date-range-panel">
      <span className="date-range-title">Date Range</span>

      <label className="date-field">
        <span>From</span>
        <input
          type="date"
          value={startDate}
          onChange={e => onStartChange(e.target.value)}
          style={inputStyle}
        />
      </label>

      <label className="date-field">
        <span>To</span>
        <input
          type="date"
          value={endDate}
          onChange={e => onEndChange(e.target.value)}
          style={inputStyle}
        />
      </label>
    </div>
  )
}

const inputStyle = {
  border: '1px solid #c8d2ea',
  borderRadius: 10,
  padding: '8px 10px',
  fontSize: 13,
  color: '#11213f',
  background: '#f7faff',
}
