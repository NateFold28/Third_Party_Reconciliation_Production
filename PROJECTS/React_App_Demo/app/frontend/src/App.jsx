import { useState, useEffect, useCallback } from 'react'
import './App.css'
import OpenRenewals     from './components/OpenRenewals.jsx'
import AllRenewals      from './components/AllRenewals.jsx'
import ModelPerformance from './components/ModelPerformance.jsx'

function monthOffset(n) {
  const d = new Date()
  d.setMonth(d.getMonth() + n)
  return d.toISOString().slice(0, 10)
}

const TABS = [
  { id: 'open',        label: 'Open Renewals',     icon: '📋' },
  { id: 'all',         label: 'All Renewals',      icon: '📊' },
  { id: 'performance', label: 'Model Performance', icon: '🎯' },
]

const ENDPOINT_MAP = {
  open:        '/api/open-renewals',
  all:         '/api/all-renewals',
  performance: '/api/model-performance',
}

const SECONDARY_MAP = {
  all:         '/api/segment-rollup',
  performance: '/api/model-runs',
}

export default function App() {
  const [activeTab,  setActiveTab]  = useState('open')
  const [startDate,  setStartDate]  = useState(monthOffset(-6))
  const [endDate,    setEndDate]    = useState(monthOffset(6))
  const [data,       setData]       = useState([])
  const [secondary,  setSecondary]  = useState([])
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState(null)
  const [refreshTs,  setRefreshTs]  = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ start_date: startDate, end_date: endDate })
      const primaryRes = await fetch(`${ENDPOINT_MAP[activeTab]}?${params}`)
      if (!primaryRes.ok) throw new Error(`API error ${primaryRes.status}`)
      const primaryJson = await primaryRes.json()
      setData(primaryJson.data ?? [])

      const secUrl = SECONDARY_MAP[activeTab]
      if (secUrl) {
        const secRes = await fetch(
          activeTab === 'performance' ? secUrl : `${secUrl}?${params}`
        )
        const secJson = secRes.ok ? await secRes.json() : { data: [] }
        setSecondary(secJson.data ?? [])
      } else {
        setSecondary([])
      }
    } catch (err) {
      setError(err.message)
      setData([])
      setSecondary([])
    } finally {
      setLoading(false)
    }
  }, [activeTab, startDate, endDate])

  useEffect(() => { fetchData() }, [fetchData])

  useEffect(() => {
    if (activeTab === 'performance' && secondary.length > 0) {
      setRefreshTs(secondary[0]?.run_timestamp ?? null)
    }
  }, [secondary, activeTab])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-logo">Portfolio Intelligence</div>
        <div className="sidebar-title">
          <span className="accent">Renewals</span> Outlook
        </div>

        <nav className="sidebar-nav">
          {TABS.map(t => (
            <button
              key={t.id}
              className={`sidebar-nav-btn${activeTab === t.id ? ' active' : ''}`}
              onClick={() => setActiveTab(t.id)}
            >
              <span className="nav-icon">{t.icon}</span>
              {t.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-section">Filters</div>

        <div className="sidebar-filter">
          <label>From</label>
          <input
            type="date"
            value={startDate}
            onChange={e => setStartDate(e.target.value)}
          />
        </div>

        <div className="sidebar-filter">
          <label>To</label>
          <input
            type="date"
            value={endDate}
            onChange={e => setEndDate(e.target.value)}
          />
        </div>

        <div className="sidebar-meta">
          {refreshTs
            ? <><strong>Data refreshed</strong><br />{refreshTs}</>
            : <><strong>Source</strong><br />V5_SANDBOX_APP_*</>
          }
          <br /><br />
          <span style={{ fontSize: 10, opacity: 0.7 }}>
            Mirrors Development_Forecast_App_V1.py
          </span>
        </div>
      </aside>

      <main className="main-content">
        <div className="hero fade-lift">
          <div className="hero-eyebrow">V5 Renewals Forecast · Dev Sandbox</div>
          <h1><span className="accent">Renewals</span> Outlook</h1>
          <p className="hero-sub">
            Open renewal contracts, forecast pipeline, and historical performance ·{' '}
            <code style={{ fontSize: 11, color: 'var(--accent-safe)' }}>
              STREAMLIT_APPS.DBO.V5_SANDBOX_APP_*
            </code>
          </p>
          <div className="hero-badge">SANDBOX</div>
        </div>

        {loading && <div className="state-msg fade-lift">Loading forecast data…</div>}
        {error   && <div className="state-msg error fade-lift">Error: {error}</div>}

        {!loading && !error && (
          <div className="fade-lift">
            {activeTab === 'open'        && <OpenRenewals rows={data} />}
            {activeTab === 'all'         && <AllRenewals rows={data} segmentRows={secondary} />}
            {activeTab === 'performance' && <ModelPerformance rows={data} runs={secondary} />}
          </div>
        )}
      </main>
    </div>
  )
}
