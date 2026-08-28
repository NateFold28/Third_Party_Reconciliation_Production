export function fmt$(v, decimals = 0) {
  const n = Number(v)
  if (!isFinite(n) || n === 0) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toLocaleString('en-US', { maximumFractionDigits: decimals })}`
}

export function fmtPct(v, decimals = 1) {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return `${n.toFixed(decimals)}%`
}

export function fmtN(v) {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return n.toLocaleString('en-US')
}

// CSS class for % values: green ≥80, yellow 60–80, red <60
export function pctClass(v) {
  const n = Number(v)
  if (!isFinite(n)) return 'clr-neutral'
  if (n >= 80) return 'clr-pos'
  if (n >= 60) return 'clr-warn'
  return 'clr-neg'
}

// CSS class for error_pp: green near 0, warn ±3, danger ±5
export function errClass(v) {
  const n = Math.abs(Number(v))
  if (!isFinite(n)) return 'clr-neutral'
  if (n <= 2) return 'clr-pos'
  if (n <= 5) return 'clr-warn'
  return 'clr-neg'
}

export function fmtMonth(dateStr) {
  if (!dateStr) return '—'
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}
/** Shared formatting helpers for table cells */

export function fmt$(value) {
  const n = Number(value)
  if (isNaN(n)) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(n)
}

export function fmtPct(value) {
  const n = Number(value)
  if (isNaN(n)) return '—'
  return `${n.toFixed(1)}%`
}

/** Returns a CSS class based on % attainment thresholds */
export function pctClass(value) {
  const n = Number(value)
  if (isNaN(n)) return ''
  if (n >= 90) return 'pct-good'
  if (n >= 70) return 'pct-warn'
  return 'pct-bad'
}
