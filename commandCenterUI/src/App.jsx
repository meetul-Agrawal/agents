import React, { useState, useEffect } from 'react'

const api = {
  get: url => fetch(url).then(r => r.json()),
  post: (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()),
}

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState(new Date())

  // Call prep modal state
  const [selCustomerForPrep, setSelCustomerForPrep] = useState(null)
  const [callPrepData, setCallPrepData] = useState(null)
  const [callPrepLoading, setCallPrepLoading] = useState(false)
  const [scriptLang, setScriptLang] = useState('hinglish')
  const [copied, setCopied] = useState(false)

  // Email modal state
  const [selCustomerForEmail, setSelCustomerForEmail] = useState(null)
  const [emailTo, setEmailTo] = useState('')
  const [emailSubject, setEmailSubject] = useState('')
  const [emailBody, setEmailBody] = useState('')
  const [emailSending, setEmailSending] = useState(false)
  const [emailSentResult, setEmailSentResult] = useState(null)
  const [emailCopied, setEmailCopied] = useState(false)

  // Message modal state (sent on UI SA)
  const [selCustomerForMsg, setSelCustomerForMsg] = useState(null)
  const [msgRecipient, setMsgRecipient] = useState('')
  const [msgChannel, setMsgChannel] = useState('chat') // 'chat' | 'whatsapp' | 'sms'
  const [msgTemplate, setMsgTemplate] = useState('overdue_notice')
  const [msgText, setMsgText] = useState('')
  const [msgSending, setMsgSending] = useState(false)
  const [msgSentResult, setMsgSentResult] = useState(null)
  const [activeConvId, setActiveConvId] = useState(null)

  // Active navigation view tab
  const [activeTab, setActiveTab] = useState('overview') // 'overview' | 'fleet' | 'portfolio' | 'stream'

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

  // Call prep opener
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

  // Email modal opener
  async function openEmailModal(customer) {
    if (!customer?.customer_id) return
    setSelCustomerForEmail(customer)
    setEmailSentResult(null)
    setEmailCopied(false)
    setEmailSending(false)

    try {
      const contact = await api.get(`/api/customers/${customer.customer_id}/contact`)
      const recipient = contact.email || 'accounts@clientdomain.com'
      setEmailTo(recipient)
      setEmailSubject(`Statement of Accounts & Overdue Settlement Notice - Gangwal Flour Foods (${customer.customer_name})`)
      setEmailBody(
`Dear Finance Team,
${customer.customer_name}

Greetings from Gangwal Flour Foods LLP.

This is a formal reminder regarding your outstanding trade balance as per our ledger books.

ACCOUNT SUMMARY:
• Customer Name: ${customer.customer_name}
• Total Outstanding: ${customer.outstanding_formatted}
• Aging Status: ${customer.ageing_bucket} (${customer.open_bills} Open Invoices)

We request you to review the attached statement and release payment for overdue invoices at the earliest.

BANK REMITTANCE DETAILS:
• Bank: HDFC Bank Ltd
• A/C Name: Gangwal Flour Foods LLP
• A/C No: 50200028192811
• IFSC Code: HDFC0000123

Kindly confirm the UTR number / payment reference once remitted.

Warm regards,
Credit & Receivables Desk
Gangwal Flour Foods LLP`
      )
    } catch (e) {
      console.error(e)
    }
  }

  async function handleSendEmail() {
    if (!selCustomerForEmail || !emailTo.trim()) return
    setEmailSending(true)
    try {
      const res = await api.post(`/api/customers/${selCustomerForEmail.customer_id}/send-email`, {
        recipient_email: emailTo,
        subject: emailSubject,
        body: emailBody,
      })
      setEmailSentResult(res)
      loadDashboard()
    } catch (e) {
      console.error(e)
    } finally {
      setEmailSending(false)
    }
  }

  // Message modal opener (sends to UI SA)
  async function openMessageModal(customer) {
    if (!customer?.customer_id) return
    setSelCustomerForMsg(customer)
    setMsgSentResult(null)
    setMsgSending(false)
    setMsgChannel('chat')
    setMsgTemplate('overdue_notice')

    try {
      const contact = await api.get(`/api/customers/${customer.customer_id}/contact`)
      setMsgRecipient(contact.mobile || '+91-9893559251')
      setActiveConvId(contact.conversation_id)
      setMsgText(`Dear ${customer.customer_name}, this is a gentle reminder regarding your pending balance of ${customer.outstanding_formatted} (${customer.open_bills} open bills). Kindly arrange settlement or let us know your planned payment date.`)
    } catch (e) {
      console.error(e)
    }
  }

  function applyMsgTemplate(tmpl) {
    setMsgTemplate(tmpl)
    if (!selCustomerForMsg) return
    if (tmpl === 'overdue_notice') {
      setMsgText(`Dear ${selCustomerForMsg.customer_name}, this is a gentle reminder regarding your pending balance of ${selCustomerForMsg.outstanding_formatted} (${selCustomerForMsg.open_bills} open bills). Kindly arrange settlement or let us know your planned payment date.`)
    } else if (tmpl === 'statement_dispatch') {
      setMsgText(`Namaste ${selCustomerForMsg.customer_name}, aapka latest ledger statement Gangwal Flour Foods se WhatsApp par share kiya gaya hai. Total dues: ${selCustomerForMsg.outstanding_formatted}. Please check and confirm.`)
    } else if (tmpl === 'promise_followup') {
      setMsgText(`Dear ${selCustomerForMsg.customer_name}, as discussed, we have noted your upcoming payment commitment. Kindly ensure clearance via RTGS/NEFT by the promised date. Thank you.`)
    } else if (tmpl === 'urgent_clearance') {
      setMsgText(`URGENT: ${selCustomerForMsg.customer_name}, multiple invoices in your account have crossed 90+ days aging (${selCustomerForMsg.outstanding_formatted}). Please clear the oldest bills today to avoid supply hold.`)
    }
  }

  async function handleSendSAMessage() {
    if (!selCustomerForMsg || !msgText.trim()) return
    setMsgSending(true)
    try {
      const res = await api.post(`/api/customers/${selCustomerForMsg.customer_id}/send-sa-message`, {
        message: msgText,
        channel: msgChannel,
        direction: 'outbound',
        conversation_id: activeConvId,
      })
      setMsgSentResult(res)
      setActiveConvId(res.conversation_id)
      loadDashboard()
    } catch (e) {
      console.error(e)
    } finally {
      setMsgSending(false)
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

  function copyEmailBody() {
    navigator.clipboard.writeText(emailBody).then(() => {
      setEmailCopied(true)
      setTimeout(() => setEmailCopied(false), 2000)
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
              <span className="tag-metric tag-emerald">380K VOUCHERS</span>
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
              <span className="tag-metric tag-indigo">16 DAYS</span>
              <span>Average portfolio settlement speed</span>
            </div>
          </div>

          {/* Company-Wide HITL Attention */}
          <div className="cc-hero-card">
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
                    <div>
                      <div className="cc-debtor-name">{c.customer_name}</div>
                      <div style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)', marginTop: '3px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span>Avg Settlement: <strong style={{ color: 'var(--ink-primary)' }}>{c.avg_settlement_formatted || 'N/A'}</strong></span>
                        <span>·</span>
                        <span>Last Paid: <strong style={{ color: 'var(--telemetry-emerald)' }}>{c.last_paid_formatted || 'No payment on record'}</strong></span>
                      </div>
                    </div>
                    <div className="cc-debtor-sum">{c.outstanding_formatted}</div>
                  </div>
                  <div className="cc-debtor-meta">
                    <div><strong>Aging Status:</strong> {c.ageing_bucket} ({c.open_bills} open invoices)</div>
                  </div>
                  <div className="cc-debtor-controls">
                    <button className="btn-call-prep-trigger" onClick={() => openCallPrep(c)} title="Generate AI Call Prep Script">
                      [CALL PREP BRIEF]
                    </button>
                    <button className="btn-action-trigger btn-email-trigger" onClick={() => openEmailModal(c)} title="Send Statement / Overdue Email">
                      [EMAIL]
                    </button>
                    <button className="btn-action-trigger btn-message-trigger" onClick={() => openMessageModal(c)} title="Send Message on UI SA Assistant">
                      [MESSAGE]
                    </button>
                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
                      <span style={{ color: 'var(--ink-secondary)' }}>Status:</span>
                      <span style={{ fontWeight: 700, textTransform: 'uppercase', color: c.risk_level === 'critical' ? 'var(--telemetry-rose)' : 'var(--telemetry-amber)' }}>
                        {c.risk_level}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Panel 2: Live Inbound Stream */}
          <div className="cc-panel-box">
            <div className="cc-panel-header">
              <div className="cc-panel-title">
                <span className="cc-title-prefix">[STREAM / AUDIT]</span>
                <span>Omnichannel Message Ingestion</span>
              </div>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>
                Live customer transactions
              </span>
            </div>

            <div className="cc-panel-scroll">
              {stream.length === 0 && (
                <div style={{ color: 'var(--ink-secondary)', textAlign: 'center', padding: '28px', fontFamily: 'var(--font-mono)' }}>
                  No inbound stream records
                </div>
              )}
              {stream.slice(0, 8).map((item, idx) => (
                <div key={item.message_id || idx} className="cc-stream-card">
                  <div className="cc-stream-heading">
                    <span>{item.customer_name}</span>
                    <span className="cc-stream-ts">
                      {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                  <div className="cc-stream-quote">
                    "{item.text}"
                  </div>
                  <div className="cc-stream-footer">
                    <span style={{ color: 'var(--ink-secondary)' }}>Intent:</span>
                    <span style={{ color: 'var(--accent-primary)', fontWeight: 700 }}>{item.intent}</span>
                    <span style={{ marginLeft: 'auto', color: 'var(--ink-secondary)' }}>Route:</span>
                    {(item.agents || []).map(a => (
                      <span key={a} className="tag-metric tag-indigo">{a}</span>
                    ))}
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
                5,000+ Ledgers
              </span>
            </div>
            <div className="cc-panel-scroll" style={{ padding: '20px 24px' }}>
              <div className="ageing-metric-stack">
                <div className="ageing-metric-row">
                  <div className="ageing-metric-header">
                    <span>0 - 30 DAYS [CURRENT TERMS]</span>
                    <span style={{ color: 'var(--telemetry-emerald)' }}>₹45,600.00</span>
                  </div>
                  <div className="ageing-meter-track"><div className="ageing-meter-fill fill-emerald" style={{ width: '5%' }}></div></div>
                </div>

                <div className="ageing-metric-row">
                  <div className="ageing-metric-header">
                    <span>31 - 60 DAYS [NORMAL BACKLOG]</span>
                    <span style={{ color: 'var(--telemetry-cyan)' }}>₹1,20,000.00</span>
                  </div>
                  <div className="ageing-meter-track"><div className="ageing-meter-fill fill-cyan" style={{ width: '10%' }}></div></div>
                </div>

                <div className="ageing-metric-row">
                  <div className="ageing-metric-header">
                    <span>61 - 90 DAYS [ACTIVE FOLLOW-UP]</span>
                    <span style={{ color: 'var(--telemetry-amber)' }}>₹4,85,200.00</span>
                  </div>
                  <div className="ageing-meter-track"><div className="ageing-meter-fill fill-amber" style={{ width: '18%' }}></div></div>
                </div>

                <div className="ageing-metric-row">
                  <div className="ageing-metric-header">
                    <span>90+ DAYS [CRITICAL CONCENTRATION]</span>
                    <span style={{ color: 'var(--telemetry-rose)' }}>{metrics.total_receivables_formatted || '₹10.58 Cr'}</span>
                  </div>
                  <div className="ageing-meter-track"><div className="ageing-meter-fill fill-rose" style={{ width: '100%' }}></div></div>
                </div>
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

      {/* ── 1. Executive Call Prep Modal ── */}
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

      {/* ── 2. Outbound Email Drafter Modal ── */}
      {selCustomerForEmail && (
        <div className="modal-overlay" onClick={() => setSelCustomerForEmail(null)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()} style={{ maxWidth: '720px' }}>
            <div className="modal-head">
              <div>
                <div className="modal-head-title">[EMAIL DISPATCH] · Statement & Overdue Notice</div>
                <div className="modal-head-sub">{selCustomerForEmail.customer_name} · Ledger Outbound</div>
              </div>
              <button className="btn-modal-close" onClick={() => setSelCustomerForEmail(null)}>[✕]</button>
            </div>

            <div className="modal-content-scroll">
              {emailSentResult ? (
                <div className="status-toast-success">
                  <span>✓ Email dispatch queued successfully to {emailSentResult.recipient}!</span>
                  <span style={{ fontSize: '11px' }}>Timestamp: {new Date(emailSentResult.sent_at).toLocaleTimeString()}</span>
                </div>
              ) : null}

              <div className="form-group">
                <label className="form-label">Recipient Email Address</label>
                <input
                  className="form-input"
                  value={emailTo}
                  onChange={e => setEmailTo(e.target.value)}
                  placeholder="accounts@customerdomain.com"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Email Subject</label>
                <input
                  className="form-input"
                  value={emailSubject}
                  onChange={e => setEmailSubject(e.target.value)}
                />
              </div>

              <div className="form-group">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <label className="form-label">Statement & Overdue Notice Body</label>
                  <button className="btn-copy-script" onClick={copyEmailBody}>
                    {emailCopied ? '[COPIED]' : '[COPY EMAIL BODY]'}
                  </button>
                </div>
                <textarea
                  className="form-textarea"
                  style={{ minHeight: '220px', fontFamily: 'var(--font-mono)', fontSize: '12px' }}
                  value={emailBody}
                  onChange={e => setEmailBody(e.target.value)}
                />
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '10px' }}>
                <button className="btn-telemetry-refresh" onClick={() => setSelCustomerForEmail(null)}>
                  Cancel
                </button>
                <button
                  className="btn-call-prep-trigger"
                  onClick={handleSendEmail}
                  disabled={emailSending || !emailTo.trim()}
                  style={{ background: 'var(--accent-primary)', padding: '8px 18px', fontSize: '12px' }}
                >
                  {emailSending ? '[SENDING DISPATCH...]' : '[SEND EMAIL DISPATCH]'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 3. Customer Assist (UI SA) Message Modal ── */}
      {selCustomerForMsg && (
        <div className="modal-overlay" onClick={() => setSelCustomerForMsg(null)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()} style={{ maxWidth: '680px' }}>
            <div className="modal-head">
              <div>
                <div className="modal-head-title">[UI SA DISPATCH] · Inbound/Outbound Message to Customer Assist</div>
                <div className="modal-head-sub">
                  Customer: {selCustomerForMsg.customer_name} · {activeConvId ? `Active Thread: ${activeConvId}` : 'New Thread'}
                </div>
              </div>
              <button className="btn-modal-close" onClick={() => setSelCustomerForMsg(null)}>[✕]</button>
            </div>

            <div className="modal-content-scroll">
              {msgSentResult ? (
                <div className="status-toast-success">
                  <span>✓ Message dispatched to UI SA Thread [{msgSentResult.conversation_id}]!</span>
                  <a
                    href={`http://localhost:5173`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: 'var(--accent-primary)', fontWeight: 700, textDecoration: 'underline' }}
                  >
                    Open UI SA Desk ↗
                  </a>
                </div>
              ) : null}

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
                <div className="form-group">
                  <label className="form-label">Customer Mobile / Identity</label>
                  <input
                    className="form-input"
                    value={msgRecipient}
                    onChange={e => setMsgRecipient(e.target.value)}
                    placeholder="+91-XXXXXXXXXX"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Dispatch Channel</label>
                  <select
                    className="form-input"
                    value={msgChannel}
                    onChange={e => setMsgChannel(e.target.value)}
                  >
                    <option value="chat">Web Chat Desk (UI SA)</option>
                    <option value="whatsapp">WhatsApp Business</option>
                    <option value="sms">Transactional SMS</option>
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Quick Smart Templates</label>
                <div className="template-chip-cluster">
                  <button
                    className={`template-chip${msgTemplate === 'overdue_notice' ? ' active' : ''}`}
                    onClick={() => applyMsgTemplate('overdue_notice')}
                  >
                    Overdue Notice
                  </button>
                  <button
                    className={`template-chip${msgTemplate === 'statement_dispatch' ? ' active' : ''}`}
                    onClick={() => applyMsgTemplate('statement_dispatch')}
                  >
                    Statement Summary
                  </button>
                  <button
                    className={`template-chip${msgTemplate === 'promise_followup' ? ' active' : ''}`}
                    onClick={() => applyMsgTemplate('promise_followup')}
                  >
                    Promise Follow-up
                  </button>
                  <button
                    className={`template-chip${msgTemplate === 'urgent_clearance' ? ' active' : ''}`}
                    onClick={() => applyMsgTemplate('urgent_clearance')}
                  >
                    Urgent Clearance
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Message Content (Dispatched to UI SA Desk)</label>
                <textarea
                  className="form-textarea"
                  value={msgText}
                  onChange={e => setMsgText(e.target.value)}
                  placeholder="Type message to dispatch to Customer Assist thread..."
                  style={{ minHeight: '110px' }}
                />
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'space-between', alignItems: 'center', marginTop: '10px' }}>
                <a
                  href={`http://localhost:5173`}
                  target="_blank"
                  rel="noreferrer"
                  className="btn-telemetry-refresh"
                  style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                >
                  [OPEN MAIN UI SA DESK ↗]
                </a>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn-telemetry-refresh" onClick={() => setSelCustomerForMsg(null)}>
                    Cancel
                  </button>
                  <button
                    className="btn-call-prep-trigger"
                    onClick={handleSendSAMessage}
                    disabled={msgSending || !msgText.trim()}
                    style={{ background: 'var(--telemetry-emerald)', padding: '8px 18px', fontSize: '12px' }}
                  >
                    {msgSending ? '[DISPATCHING TO SA...]' : '[SEND MESSAGE ON UI SA]'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
