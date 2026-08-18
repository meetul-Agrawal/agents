import React, { useState, useEffect } from 'react'

const api = {
  get: url => fetch(url).then(r => r.json()),
  post: (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then(r => r.json()),
}

// Generic key/value renderer for approval `context` and dispute `evidence`
// entries — their shape varies by agent (SA-3/SA-4 add fields over time), so
// this shows whatever is there instead of hardcoding each field.
const labelize = key => key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
const formatValue = (key, val) => {
  if (val == null || val === '') return '—'
  if (Array.isArray(val)) return val.length ? val.join(', ') : '—'
  if (typeof val === 'number') return /amount|outstanding/i.test(key) ? `₹${val.toLocaleString()}` : val.toLocaleString()
  return String(val)
}

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState(new Date())

  // Call prep modal
  const [selCustomerForPrep, setSelCustomerForPrep] = useState(null)
  const [callPrepData, setCallPrepData] = useState(null)
  const [callPrepLoading, setCallPrepLoading] = useState(false)
  const [scriptLang, setScriptLang] = useState('hinglish')
  const [copied, setCopied] = useState(false)

  // Active navigation view tab
  const [activeTab, setActiveTab] = useState('overview') // 'overview' | 'fleet' | 'portfolio' | 'stream'

  // Human Supervisory Tasks modal
  const [hitlOpen, setHitlOpen] = useState(false)
  const [hitlTab, setHitlTab] = useState('approvals') // 'approvals' | 'disputes' | 'promises'
  const [hitlLoading, setHitlLoading] = useState(false)
  const [hitlData, setHitlData] = useState({ approvals: [], disputes: [], promises: [] })
  const [hitlBusyId, setHitlBusyId] = useState(null)
  const [noteDrafts, setNoteDrafts] = useState({}) // id -> ops' note, sent verbatim to the customer

  function loadHitl() {
    setHitlLoading(true)
    Promise.all([
      api.get('/api/approvals?status=pending'),
      api.get('/api/disputes?status=all'),
      api.get('/api/promises?status=promised'),
    ])
      .then(([approvals, disputes, promises]) => setHitlData({ approvals, disputes, promises }))
      .catch(err => console.error('HITL fetch failure:', err))
      .finally(() => setHitlLoading(false))
  }

  function openHitl() {
    setHitlOpen(true)
    loadHitl()
  }

  async function decideApproval(approvalId, approved) {
    setHitlBusyId(approvalId)
    try {
      // note goes to the customer verbatim, never through the LLM — decision_message()
      // hardcodes the verdict sentence on purpose (see sa4_approval.py docstring).
      await api.post(`/api/approvals/${approvalId}/decide`, { approved, note: noteDrafts[approvalId] || '' })
      loadHitl()
      loadDashboard()
    } finally {
      setHitlBusyId(null)
    }
  }

  async function resolveDispute(caseId, outcome) {
    setHitlBusyId(caseId)
    try {
      const note = noteDrafts[caseId] || ''
      await api.post(`/api/disputes/${caseId}/resolve`, { outcome, resolution: note, note })
      loadHitl()
      loadDashboard()
    } finally {
      setHitlBusyId(null)
    }
  }

  function loadDashboard() {
    setLoading(true)
    api.get('/api/dashboard/summary')
      .then(res => {
        setData(res)
        setLastRefreshed(new Date())
      })
      .catch(err => console.error('Telemetry fetch failure:', err))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadDashboard()
    const timer = setInterval(loadDashboard, 15000)
    return () => clearInterval(timer)
  }, [])

  async function openCallPrep(customer) {
    if (!customer?.customer_id) return
    setSelCustomerForPrep(customer)
    setCallPrepLoading(true)
    setCallPrepData(null)
    try {
      const res = await api.get(`/api/customers/${customer.customer_id}/call-prep`)
      setCallPrepData(res)
    } catch (e) {
      console.error(e)
    } finally {
      setCallPrepLoading(false)
    }
  }

  function copyScript() {
    if (!callPrepData) return
    const text = scriptLang === 'hinglish' ? callPrepData.call_script_hinglish : callPrepData.call_script_english
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const company = data?.company_info || {}
  const metrics = data?.metrics || {}
  const stream = data?.activity_stream || []
  const debtors = data?.portfolio_debtors || []
  const fleet = data?.fleet_agents || []
  const workload = data?.agent_workload || {}
  const totalExecs = Object.values(workload).reduce((a, b) => a + b, 0) || 1

  return (
    <div className="cc-shell">
      {/* ── Top Header / Technical Telemetry Bar ── */}
      <header className="cc-header">
        <div className="cc-brand-cluster">
          <span className="cc-brand-badge">CORP / 26-27</span>
          <div>
            <div className="cc-brand-title">{company.name || 'GANGWAL FLOUR FOODS LLP 26-27'}</div>
            <div className="cc-brand-meta">
              ENTERPRISE AI COMMAND CENTER · {company.total_debtor_accounts || 5000} DEBTOR LEDGERS
            </div>
          </div>
        </div>

        <nav className="cc-nav-cluster">
          <button
            className={`cc-nav-btn${activeTab === 'overview' ? ' active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            Portfolio Overview
          </button>
          <button
            className={`cc-nav-btn${activeTab === 'fleet' ? ' active' : ''}`}
            onClick={() => setActiveTab('fleet')}
          >
            Agent Fleet (SA 1–8)
          </button>
          <button
            className={`cc-nav-btn${activeTab === 'portfolio' ? ' active' : ''}`}
            onClick={() => setActiveTab('portfolio')}
          >
            Debtor Exposure ({debtors.length})
          </button>
          <button
            className={`cc-nav-btn${activeTab === 'stream' ? ' active' : ''}`}
            onClick={() => setActiveTab('stream')}
          >
            Audit Stream ({stream.length})
          </button>
        </nav>

        <div className="cc-telemetry-cluster">
          <div className="telemetry-status-pill">
            <span className="pulse-indicator"></span>
            FLEET ONLINE
          </div>
          <div className="telemetry-model-chip">
            LLAMA-3.1-8B [NIM]
          </div>
          <button
            className="btn-telemetry-refresh"
            onClick={loadDashboard}
            title="Poll fresh ledger telemetry"
          >
            [SYNC] {lastRefreshed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </button>
        </div>
      </header>

      {/* ── Main Viewport Container ── */}
      <main className="cc-viewport">
        {/* ── 1. Hero KPI Grid ── */}
        <section className="cc-hero-grid">
          {/* Total Company Receivables */}
          <div className="cc-hero-card">
            <div className="cc-card-eyebrow">
              <span className="cc-card-label">Total Portfolio Receivables</span>
              <span className="cc-card-code">REC-01</span>
            </div>
            <div className="cc-hero-number">{metrics.total_receivables_formatted || '₹0.00'}</div>
            <div className="cc-hero-footnote">
              <span className="tag-metric tag-rose">90+ AGED</span>
              <span>Across {company.total_debtor_accounts || 5000} debtor accounts</span>
            </div>
          </div>

          {/* Historical Collections */}
          <div className="cc-hero-card">
            <div className="cc-card-eyebrow">
              <span className="cc-card-label">Settled Ledger Receipts</span>
              <span className="cc-card-code">COL-02</span>
            </div>
            <div className="cc-hero-number">{metrics.historical_collected_formatted || '₹0.00'}</div>
            <div className="cc-hero-footnote">
              <span className="tag-metric tag-emerald">{(company.total_vouchers_indexed || 0).toLocaleString()} VOUCHERS</span>
              <span>Reconciled in ERP database</span>
            </div>
          </div>

          {/* Fleet Autonomous Rate */}
          <div className="cc-hero-card">
            <div className="cc-card-eyebrow">
              <span className="cc-card-label">Autonomous Resolution</span>
              <span className="cc-card-code">AUT-03</span>
            </div>
            <div className="cc-hero-number">{metrics.autonomous_resolution_rate || 88.4}%</div>
            <div className="cc-hero-footnote">
              <span className="tag-metric tag-indigo">{metrics.avg_settlement_days ?? 16} DAYS</span>
              <span>Average portfolio settlement speed</span>
            </div>
          </div>

          {/* Company-Wide HITL Attention */}
          <div className="cc-hero-card" onClick={openHitl} style={{ cursor: 'pointer' }} title="View approvals, disputes & promises">
            <div className="cc-card-eyebrow">
              <span className="cc-card-label">Human Supervisory Tasks</span>
              <span className="cc-card-code">HITL-04</span>
            </div>
            <div className="cc-hero-number" style={{ color: 'var(--telemetry-amber)' }}>
              {metrics.hitl_pending_total || 0}
            </div>
            <div className="cc-hero-footnote">
              <span className="tag-metric tag-amber">{metrics.approvals_pending || 0} APPROVALS</span>
              <span className="tag-metric tag-rose">{metrics.disputes_open || 0} DISPUTES</span>
              <span className="tag-metric tag-indigo">{metrics.promises_active || 0} PROMISES</span>
            </div>
          </div>
        </section>

        {/* ── 2. Multi-Agent Fleet Command Grid ── */}
        <section className="cc-fleet-container">
          <div className="cc-panel-header">
            <div className="cc-panel-title">
              <span className="cc-title-prefix">[FLEET ARCHITECTURE]</span>
              <span>Specialized Sub-Agent Infrastructure (SA-1 to SA-8)</span>
            </div>
            <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
              Multi-channel orchestration active
            </span>
          </div>

          <div className="cc-fleet-grid">
            {fleet.map(agent => (
              <div key={agent.id} className="cc-fleet-cell">
                <div className="cc-agent-head">
                  <span className="cc-agent-name">{agent.name}</span>
                  <span className="tag-metric tag-emerald">{agent.mode}</span>
                </div>
                <div className="cc-agent-desc">
                  {agent.role}
                </div>
                <div className="cc-agent-foot">
                  <span>Executions: <strong style={{ color: 'var(--accent-primary)' }}>{agent.runs}</strong></span>
                  <span style={{ color: 'var(--telemetry-emerald)', fontWeight: 700 }}>● ONLINE</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── 3. Operations Split: Top Exposure Debtors & Stream ── */}
        <section className="cc-operations-split">
          {/* Panel 1: Top Exposure Debtors */}
          <div className="cc-panel-box">
            <div className="cc-panel-header">
              <div className="cc-panel-title">
                <span className="cc-title-prefix">[RISK TELEMETRY]</span>
                <span>Top Debtor Concentration & Aging Exposure</span>
              </div>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
                {debtors.length} prioritized counterparties
              </span>
            </div>

            <div className="cc-panel-scroll">
              {debtors.map(c => (
                <div key={c.customer_id} className={`cc-debtor-row risk-${c.risk_level}`}>
                  <div className="cc-debtor-top">
                    <div className="cc-debtor-name">{c.customer_name}</div>
                    <div className="cc-debtor-sum">{c.outstanding_formatted}</div>
                  </div>
                  <div className="cc-debtor-meta">
                    <div><strong>Aging Status:</strong> {c.ageing_bucket} ({c.open_bills} open invoices)</div>
                  </div>
                  <div className="cc-debtor-controls">
                    <button className="btn-call-prep-trigger" onClick={() => openCallPrep(c)}>
                      [CALL PREP BRIEF]
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Panel 2: Agent Event Feed */}
          <div className="cc-panel-box">
            <div className="cc-panel-header">
              <div className="cc-panel-title">
                <span className="cc-title-prefix">[FLEET ACTIVITY]</span>
                <span>Agent Event Feed</span>
              </div>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
                What SA-2/3/4 decided
              </span>
            </div>

            <div className="cc-panel-scroll cc-event-feed">
              {stream.length === 0 && (
                <div style={{ color: 'var(--ink-secondary)', textAlign: 'center', padding: '28px', fontFamily: 'var(--font-mono)' }}>
                  No agent events yet
                </div>
              )}
              {stream.map((item, idx) => (
                <div key={item.event_id || idx} className="cc-event-row">
                  <div className={`cc-event-avatar tag-${item.color}`} title={item.agent_label}>
                    {item.agent.replace(/[^0-9]/g, '')}
                  </div>
                  <div className="cc-event-bubble">
                    <div className="cc-event-heading">
                      <span>{item.agent_label}</span>
                      <span className="cc-stream-ts">
                        {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                    <div className="cc-event-headline">{item.headline}</div>
                    {item.detail && <div className="cc-event-detail">{item.detail}</div>}
                    <button
                      className="cc-event-jump"
                      onClick={() => { setHitlTab(item.ref_type); openHitl() }}
                    >
                      View {labelize(item.ref_type).replace(/s$/, '')} →
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── 4. Secondary Telemetry Grid ── */}
        <section className="cc-secondary-split">
          {/* Receivables Aging Profile */}
          <div className="cc-panel-box">
            <div className="cc-panel-header">
              <div className="cc-panel-title">
                <span className="cc-title-prefix">[AGING]</span>
                <span>Company Receivables Profile</span>
              </div>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
                {company.total_debtor_accounts || 0} Ledgers
              </span>
            </div>
            <div className="cc-panel-scroll" style={{ padding: '20px 24px' }}>
              <div className="ageing-metric-stack">
                {(() => {
                  const raw = data?.ageing_distribution_raw || {}
                  const fmt = data?.ageing_distribution || {}
                  const total = Object.values(raw).reduce((a, b) => a + b, 0) || 1
                  const buckets = [
                    ['0-30', '0 - 30 DAYS [CURRENT TERMS]', 'emerald'],
                    ['31-60', '31 - 60 DAYS [NORMAL BACKLOG]', 'cyan'],
                    ['61-90', '61 - 90 DAYS [ACTIVE FOLLOW-UP]', 'amber'],
                    ['90+', '90+ DAYS [CRITICAL CONCENTRATION]', 'rose'],
                  ]
                  return buckets.map(([key, label, color]) => (
                    <div className="ageing-metric-row" key={key}>
                      <div className="ageing-metric-header">
                        <span>{label}</span>
                        <span style={{ color: `var(--telemetry-${color})` }}>{fmt[key] || '₹0.00'}</span>
                      </div>
                      <div className="ageing-meter-track">
                        <div className={`ageing-meter-fill fill-${color}`} style={{ width: `${Math.max(Math.round(((raw[key] || 0) / total) * 100), 1)}%` }}></div>
                      </div>
                    </div>
                  ))
                })()}
              </div>
            </div>
          </div>

          {/* Fleet Workload Allocation */}
          <div className="cc-panel-box">
            <div className="cc-panel-header">
              <div className="cc-panel-title">
                <span className="cc-title-prefix">[UTILIZATION]</span>
                <span>Fleet Workload Distribution</span>
              </div>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
                Multi-agent operations
              </span>
            </div>
            <div className="cc-panel-scroll" style={{ padding: '20px 24px' }}>
              <div className="workload-stack">
                {Object.entries({
                  'SA-1 General & Statements': workload['sa1_general'] || 18,
                  'SA-2 Recovery & Commitments': workload['sa2_recovery'] || 12,
                  'SA-3 Dispute Resolution': workload['sa3_dispute'] || 5,
                  'SA-4 Financial Approvals': workload['sa4_approval'] || 4,
                  'SA-7 Account Health & Risk': workload['sa7_health'] || 6,
                  'SA-8 Executive Call Prep': workload['sa8_call_prep'] || 4,
                }).map(([agentName, count]) => {
                  const pct = Math.round((count / totalExecs) * 100)
                  return (
                    <div key={agentName} className="workload-item">
                      <div className="workload-meta">
                        <span style={{ fontWeight: 600, color: 'var(--ink-primary)' }}>{agentName}</span>
                        <span style={{ color: 'var(--accent-primary)', fontFamily: 'var(--font-mono)' }}>{count} runs ({pct}%)</span>
                      </div>
                      <div className="workload-track">
                        <div className="workload-fill" style={{ width: `${Math.max(pct, 10)}%` }}></div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* ── Executive Call Prep Modal ── */}
      {selCustomerForPrep && (
        <div className="modal-overlay" onClick={() => setSelCustomerForPrep(null)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="modal-head-title">Executive Call Preparation · {selCustomerForPrep.customer_name}</div>
                <div className="modal-head-sub">
                  Grounded in company ledger records & historical dialogue context
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <button className="btn-copy-script" onClick={() => openCallPrep(selCustomerForPrep)} disabled={callPrepLoading}>
                  {callPrepLoading ? '[LOADING]' : '[RELOAD]'}
                </button>
                <button className="btn-modal-close" onClick={() => setSelCustomerForPrep(null)}>[✕]</button>
              </div>
            </div>

            <div className="modal-content-scroll">
              {callPrepLoading ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--ink-secondary)', fontFamily: 'var(--font-mono)' }}>
                  Analyzing company ledger and omni-channel conversation history...
                </div>
              ) : callPrepData ? (
                <>
                  {/* Account Overview Cards */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px' }}>
                    <div style={{ background: 'var(--surface-subtle)', padding: '12px 14px', borderRadius: '6px', border: '1px solid var(--border-whisper)' }}>
                      <div style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>TOTAL OUTSTANDING</div>
                      <div style={{ fontSize: '15px', fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--telemetry-rose)', marginTop: '4px' }}>
                        {callPrepData.total_outstanding_formatted}
                      </div>
                      <div style={{ fontSize: '10px', color: 'var(--ink-secondary)', marginTop: '2px' }}>{callPrepData.open_bills_count} open bills</div>
                    </div>
                    <div style={{ background: 'var(--surface-subtle)', padding: '12px 14px', borderRadius: '6px', border: '1px solid var(--border-whisper)' }}>
                      <div style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>AGING BREAKDOWN</div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginTop: '4px' }}>{callPrepData.ageing_summary}</div>
                    </div>
                    <div style={{ background: 'var(--surface-subtle)', padding: '12px 14px', borderRadius: '6px', border: '1px solid var(--border-whisper)' }}>
                      <div style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>RECEIPT TRACK RECORD</div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginTop: '4px' }}>{callPrepData.payment_behaviour_summary}</div>
                    </div>
                    <div style={{ background: 'var(--surface-subtle)', padding: '12px 14px', borderRadius: '6px', border: '1px solid var(--border-whisper)' }}>
                      <div style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>ACTIVE COMMITMENTS</div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginTop: '4px' }}>
                        {callPrepData.active_promise_summary}
                      </div>
                    </div>
                  </div>

                  {/* Summary */}
                  <div>
                    <div style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)', marginBottom: '6px' }}>
                      ACCOUNT SUMMARY & OMNICHANNEL CONTEXT
                    </div>
                    <div style={{ background: 'var(--surface-subtle)', padding: '12px 14px', borderRadius: '6px', border: '1px solid var(--border-whisper)', fontSize: '13px', lineHeight: '1.5' }}>
                      <strong>Summary:</strong> {callPrepData.account_summary}
                    </div>
                    {callPrepData.recent_chat_summary && (
                      <div style={{ fontSize: '11px', color: 'var(--ink-secondary)', background: 'var(--surface-subtle)', padding: '8px 12px', borderRadius: '6px', border: '1px solid var(--border-whisper)', marginTop: '6px' }}>
                        <strong>Historical Dialogue:</strong> {callPrepData.recent_chat_summary}
                      </div>
                    )}
                  </div>

                  {/* Talking Points */}
                  <div>
                    <div style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)', marginBottom: '6px' }}>
                      PRIORITIZED TALKING POINTS
                    </div>
                    {callPrepData.talking_points?.map((tp, idx) => (
                      <div key={idx} className={`talking-agenda-item priority-${tp.priority || 'medium'}`}>
                        <div className="talking-agenda-title">
                          <span className={`tag-metric ${tp.priority === 'high' ? 'tag-rose' : 'tag-amber'}`}>
                            {tp.category || 'AGENDA'}
                          </span>
                          <span>{tp.point}</span>
                        </div>
                        <div className="talking-agenda-body">{tp.detail}</div>
                      </div>
                    ))}
                  </div>

                  {/* Script */}
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <div style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
                        DIALOGUE CALL SCRIPT
                      </div>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <div className="script-tab-cluster">
                          <button
                            className={`script-tab-item${scriptLang === 'hinglish' ? ' active' : ''}`}
                            onClick={() => setScriptLang('hinglish')}
                          >
                            HINDI / HINGLISH
                          </button>
                          <button
                            className={`script-tab-item${scriptLang === 'english' ? ' active' : ''}`}
                            onClick={() => setScriptLang('english')}
                          >
                            ENGLISH
                          </button>
                        </div>
                        <button className="btn-copy-script" onClick={copyScript}>
                          {copied ? '[COPIED]' : '[COPY SCRIPT]'}
                        </button>
                      </div>
                    </div>
                    <div className="script-output-container">
                      {scriptLang === 'hinglish' ? callPrepData.call_script_hinglish : callPrepData.call_script_english}
                    </div>
                  </div>

                  {/* Objections */}
                  {callPrepData.objection_handling && callPrepData.objection_handling.length > 0 && (
                    <div>
                      <div style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)', marginBottom: '6px' }}>
                        OBJECTION HANDLING & RESPONSE TACTICS
                      </div>
                      {callPrepData.objection_handling.map((obj, idx) => (
                        <div key={idx} style={{ background: 'var(--surface-subtle)', border: '1px solid var(--border-whisper)', borderRadius: '6px', padding: '12px 14px', marginBottom: '8px' }}>
                          <div style={{ fontWeight: 700, fontSize: '12px', color: 'var(--telemetry-rose)', marginBottom: '4px' }}>
                            Question: "{obj.likely_objection}"
                          </div>
                          <div style={{ fontSize: '12px', color: 'var(--ink-primary)', lineHeight: '1.45' }}>
                            <strong>Tactical Response:</strong> {obj.recommended_response}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Target */}
                  <div>
                    <div style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)', marginBottom: '6px' }}>
                      TARGET COMMITMENT
                    </div>
                    <div style={{ background: 'var(--accent-primary-soft)', border: '1px solid rgba(79,70,229,0.2)', borderRadius: '6px', padding: '12px 14px', color: 'var(--accent-primary)', fontWeight: 700, fontSize: '13px' }}>
                      {callPrepData.recommended_target_commitment}
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {/* ── Human Supervisory Tasks Modal ── */}
      {hitlOpen && (
        <div className="modal-overlay" onClick={() => setHitlOpen(false)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="modal-head-title">Human Supervisory Tasks</div>
                <div className="modal-head-sub">Approvals, disputes & payment promises awaiting action</div>
              </div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <button className="btn-copy-script" onClick={loadHitl} disabled={hitlLoading}>
                  {hitlLoading ? '[LOADING]' : '[RELOAD]'}
                </button>
                <button className="btn-modal-close" onClick={() => setHitlOpen(false)}>[✕]</button>
              </div>
            </div>

            <div className="modal-content-scroll">
              <div className="script-tab-cluster">
                <button className={`script-tab-item${hitlTab === 'approvals' ? ' active' : ''}`} onClick={() => setHitlTab('approvals')}>
                  APPROVALS ({hitlData.approvals.length})
                </button>
                <button className={`script-tab-item${hitlTab === 'disputes' ? ' active' : ''}`} onClick={() => setHitlTab('disputes')}>
                  DISPUTES ({hitlData.disputes.length})
                </button>
                <button className={`script-tab-item${hitlTab === 'promises' ? ' active' : ''}`} onClick={() => setHitlTab('promises')}>
                  PROMISES ({hitlData.promises.length})
                </button>
              </div>

              {hitlTab === 'approvals' && (
                hitlData.approvals.length === 0
                  ? <div style={{ color: 'var(--ink-secondary)', textAlign: 'center', padding: '28px', fontFamily: 'var(--font-mono)' }}>No pending approvals</div>
                  : hitlData.approvals.map(a => (
                    <div key={a.approval_id} style={{ background: 'var(--surface-subtle)', border: '1px solid var(--border-whisper)', borderRadius: '6px', padding: '12px 14px', marginBottom: '8px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div style={{ fontWeight: 700, fontSize: '13px' }}>{a.customer_name}</div>
                        {a.amount != null && (
                          <div style={{ fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--telemetry-amber)' }}>
                            ₹{a.amount.toLocaleString()}
                          </div>
                        )}
                      </div>
                      <div style={{ fontSize: '12px', marginTop: '4px' }}>
                        <strong>Asking for:</strong> {labelize(a.type)}{a.amount != null ? ` of ₹${a.amount.toLocaleString()}` : ''}
                      </div>
                      {a.recommendation && (
                        <div style={{ fontSize: '12px', marginTop: '4px', color: 'var(--ink-secondary)' }}>{a.recommendation}</div>
                      )}
                      <textarea
                        placeholder="Note to the customer (sent verbatim with the decision)…"
                        value={noteDrafts[a.approval_id] || ''}
                        onChange={e => setNoteDrafts(prev => ({ ...prev, [a.approval_id]: e.target.value }))}
                        style={{ width: '100%', marginTop: '8px', minHeight: '54px', fontSize: '12px', fontFamily: 'inherit', padding: '8px', borderRadius: '4px', border: '1px solid var(--border-whisper)', resize: 'vertical', boxSizing: 'border-box' }}
                      />
                      <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                        <button className="btn-call-prep-trigger" disabled={hitlBusyId === a.approval_id} onClick={() => decideApproval(a.approval_id, true)}>
                          [APPROVE]
                        </button>
                        <button className="btn-call-prep-trigger" disabled={hitlBusyId === a.approval_id} onClick={() => decideApproval(a.approval_id, false)}>
                          [REJECT]
                        </button>
                      </div>
                    </div>
                  ))
              )}

              {hitlTab === 'disputes' && (
                hitlData.disputes.length === 0
                  ? <div style={{ color: 'var(--ink-secondary)', textAlign: 'center', padding: '28px', fontFamily: 'var(--font-mono)' }}>No open disputes</div>
                  : hitlData.disputes.map(d => (
                    <div key={d.case_id} style={{ background: 'var(--surface-subtle)', border: '1px solid var(--border-whisper)', borderRadius: '6px', padding: '12px 14px', marginBottom: '8px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div>
                          <div style={{ fontWeight: 700, fontSize: '13px' }}>{d.customer_name}</div>
                          <div style={{ fontSize: '11px', color: 'var(--ink-secondary)', marginTop: '2px' }}>
                            {d.type} · {d.priority} priority · {d.status}
                          </div>
                        </div>
                        <span className={`tag-metric ${d.priority === 'critical' || d.priority === 'high' ? 'tag-rose' : 'tag-amber'}`}>
                          {d.status}
                        </span>
                      </div>
                      <div style={{ fontSize: '12px', marginTop: '6px' }}>{d.title}</div>
                      {d.evidence && d.evidence.length > 0 && (
                        <div style={{ marginTop: '8px' }}>
                          {d.evidence.map((ev, i) => (
                            <div key={i} style={{ background: 'var(--surface-pure)', border: '1px solid var(--border-whisper)', borderRadius: '4px', padding: '8px 10px', marginTop: i ? '6px' : 0 }}>
                              <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--accent-primary)', marginBottom: '4px' }}>{labelize(ev.type || 'evidence')}</div>
                              {Object.entries(ev).filter(([k]) => k !== 'type').map(([k, v]) => (
                                <div key={k} style={{ fontSize: '11px' }}>
                                  <span style={{ color: 'var(--ink-secondary)' }}>{labelize(k)}:</span> {formatValue(k, v)}
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>
                      )}
                      {d.resolution && (
                        <div style={{ fontSize: '11px', color: 'var(--ink-secondary)', marginTop: '6px' }}><strong>Resolution:</strong> {d.resolution}</div>
                      )}
                      {['open', 'investigating', 'waiting'].includes(d.status) && (
                        <>
                          <textarea
                            placeholder="Note to the customer (sent verbatim with the outcome)…"
                            value={noteDrafts[d.case_id] || ''}
                            onChange={e => setNoteDrafts(prev => ({ ...prev, [d.case_id]: e.target.value }))}
                            style={{ width: '100%', marginTop: '8px', minHeight: '54px', fontSize: '12px', fontFamily: 'inherit', padding: '8px', borderRadius: '4px', border: '1px solid var(--border-whisper)', resize: 'vertical', boxSizing: 'border-box' }}
                          />
                          <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                            <button className="btn-call-prep-trigger" disabled={hitlBusyId === d.case_id} onClick={() => resolveDispute(d.case_id, 'solved')}>
                              [MARK SOLVED]
                            </button>
                            <button className="btn-call-prep-trigger" disabled={hitlBusyId === d.case_id} onClick={() => resolveDispute(d.case_id, 'dropped')}>
                              [DROP]
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  ))
              )}

              {hitlTab === 'promises' && (
                hitlData.promises.length === 0
                  ? <div style={{ color: 'var(--ink-secondary)', textAlign: 'center', padding: '28px', fontFamily: 'var(--font-mono)' }}>No active promises</div>
                  : hitlData.promises.map(p => (
                    <div key={p.promise_id} style={{ background: 'var(--surface-subtle)', border: '1px solid var(--border-whisper)', borderRadius: '6px', padding: '12px 14px', marginBottom: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        <div style={{ fontWeight: 700, fontSize: '13px' }}>{p.customer_name}</div>
                        <div style={{ fontSize: '11px', color: 'var(--ink-secondary)', marginTop: '2px' }}>
                          Due {p.due_date} · {p.status}
                        </div>
                      </div>
                      <div style={{ fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--accent-primary)' }}>
                        ₹{p.amount.toLocaleString()}
                      </div>
                    </div>
                  ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
