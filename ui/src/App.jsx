import { useState, useEffect, useRef } from 'react'

const api = {
  get: url => fetch(url).then(r => r.json()),
  post: (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()),
}

function fmt(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) +
    ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function inr(amount) {
  if (amount == null) return null
  return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const APPROVAL_TYPE_LABEL = {
  special_discount: 'Special discount', settlement: 'Settlement', credit_limit: 'Credit limit',
  large_credit_note: 'Credit note', write_off: 'Write-off', exceptional_terms: 'Payment terms',
}

// ── Approval detail — approve / reject ──────────────────────────────────────

function ApprovalDetail({ approval, onDecided }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(null)

  useEffect(() => { setNote(''); setSent(null) }, [approval?.approval_id])

  if (!approval) return <div className="no-thread">← Pick a pending approval</div>

  async function decide(approved) {
    setBusy(true)
    try {
      const res = await api.post(`/api/approvals/${approval.approval_id}/decide`, {
        approved, decided_by: 'ops', note,
      })
      if (res.ok) { setSent(res); onDecided() }
    } finally {
      setBusy(false)
    }
  }

  const context = Object.entries(approval.context || {})
  const decided = approval.status !== 'pending'

  return (
    <div className="detail-view">
      <div className="detail-title">{APPROVAL_TYPE_LABEL[approval.type] ?? approval.type}</div>
      <div className="detail-sub">
        {approval.customer_name} · {approval.approval_id}
        {inr(approval.amount) && <> · {inr(approval.amount)}</>}
        {' · '}<span className={`badge ${
          approval.status === 'approved' ? 'b-green' : approval.status === 'rejected' ? 'b-red' : 'b-yellow'
        }`}>{approval.status}</span>
      </div>

      <div className="sec">
        <div className="sec-title">Recommendation</div>
        <div className="final-resp">{approval.recommendation || 'No recommendation recorded.'}</div>
      </div>

      {context.length > 0 && (
        <div className="sec">
          <div className="sec-title">Context</div>
          <ul className="detail-list">
            {context.map(([k, v]) => <li key={k}>{k.replace(/_/g, ' ')}: {String(v)}</li>)}
          </ul>
        </div>
      )}

      {!decided && !sent && (
        <>
          <div className="sec-title">Note to customer (optional)</div>
          <textarea className="detail-note" value={note} onChange={e => setNote(e.target.value)}
            placeholder="Optional context to include in the customer's reply…" disabled={busy} />
          <div className="detail-actions">
            <button className="dbtn dbtn-approve" disabled={busy} onClick={() => decide(true)}>✓ Approve</button>
            <button className="dbtn dbtn-reject" disabled={busy} onClick={() => decide(false)}>✗ Reject</button>
          </div>
        </>
      )}

      {sent && (
        <div className="sent-box">
          <div className="sent-label">{sent.message_sent ? 'Sent to customer' : 'Decision recorded — no conversation to notify'}</div>
          {sent.message_sent && sent.message_text}
        </div>
      )}

      {decided && !sent && (
        <div className="sent-box">
          <div className="sent-label">Already decided</div>
          Decided by {approval.decided_by} on {fmt(approval.decided_at)}.
        </div>
      )}
    </div>
  )
}

// ── Dispute detail — solved / dropped ───────────────────────────────────────

function DisputeDetail({ case_, onResolved }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(null)

  useEffect(() => { setNote(''); setSent(null) }, [case_?.case_id])

  if (!case_) return <div className="no-thread">← Pick an open dispute</div>

  async function resolve(outcome) {
    setBusy(true)
    try {
      const res = await api.post(`/api/disputes/${case_.case_id}/resolve`, {
        outcome, resolution: note, note: '',
      })
      if (res.ok) { setSent(res); onResolved() }
    } finally {
      setBusy(false)
    }
  }

  const decided = case_.status !== 'open' && case_.status !== 'investigating' && case_.status !== 'waiting'

  return (
    <div className="detail-view">
      <div className="detail-title">{case_.title}</div>
      <div className="detail-sub">
        {case_.customer_name} · {case_.case_id}
        {' · '}<span className={`badge ${case_.priority === 'high' || case_.priority === 'critical' ? 'b-red' : 'b-blue'}`}>{case_.priority}</span>
        {' · '}<span className={`badge ${
          case_.status === 'resolved' ? 'b-green' : case_.status === 'closed' ? 'b-gray' : 'b-yellow'
        }`}>{case_.status}</span>
      </div>

      <div className="sec">
        <div className="sec-title">Evidence</div>
        {(!case_.evidence || case_.evidence.length === 0) ? (
          <span className="muted">none recorded</span>
        ) : (
          <ul className="detail-list">
            {case_.evidence.map((e, i) => (
              <li key={i}>
                {e.type === 'voucher_not_found' && <>Could not find {e.voucher_number} on the account.</>}
                {e.type === 'invoice_on_record' && <>Invoice {e.voucher_number}: {inr(e.amount)} on {e.date}.</>}
                {e.type === 'receipt_on_record' && <>Receipt against {e.voucher_number}: {inr(e.amount)}.</>}
                {e.type === 'outstanding_snapshot' && <>Outstanding: {inr(e.outstanding)} across {e.open_bill_count} invoice(s).</>}
                {!['voucher_not_found', 'invoice_on_record', 'receipt_on_record', 'outstanding_snapshot'].includes(e.type) &&
                  JSON.stringify(e)}
              </li>
            ))}
          </ul>
        )}
      </div>

      {case_.resolution && (
        <div className="sec">
          <div className="sec-title">Resolution</div>
          <div className="final-resp">{case_.resolution}</div>
        </div>
      )}

      {!decided && !sent && (
        <>
          <div className="sec-title">Resolution note</div>
          <textarea className="detail-note" value={note} onChange={e => setNote(e.target.value)}
            placeholder="What was found / what happens next…" disabled={busy} />
          <div className="detail-actions">
            <button className="dbtn dbtn-approve" disabled={busy} onClick={() => resolve('solved')}>✓ Solved</button>
            <button className="dbtn dbtn-drop" disabled={busy} onClick={() => resolve('dropped')}>⨯ Dropped</button>
          </div>
        </>
      )}

      {sent && (
        <div className="sent-box">
          <div className="sent-label">{sent.message_sent ? 'Sent to customer' : 'Case updated — no conversation to notify'}</div>
          {sent.message_sent && sent.message_text}
        </div>
      )}
    </div>
  )
}

// ── Right panel ───────────────────────────────────────────────────────────────

function RightPanel({ cls, classifier, activeConv, lastInput, customerId, allIntents }) {
  const [partial, setPartial] = useState(false)
  const [checked, setChecked] = useState({})
  const [done, setDone] = useState(null)

  useEffect(() => { setPartial(false); setChecked({}); setDone(null) }, [cls])

  if (!cls) {
    return <div className="empty-right">Send a message to see<br />the classification here</div>
  }

  const intents = cls.intents_detail || []
  const entities = Object.entries(cls.entities || {}).filter(([k]) => k !== 'message_id')
  const allFields = [
    ...intents.map(i => 'intent:' + i.name),
    'agents', 'requires_human', 'entities', 'urgency',
  ]

  function toggle(key, val) { setChecked(p => ({ ...p, [key]: val })) }

  function buildExpected() {
    return {
      intent: cls.intent,
      intents: cls.intents,
      agents: cls.agents,
      requires_human: cls.requires_human,
      entities: cls.entities,
      urgency: cls.urgency,
    }
  }

  async function submit(label) {
    const correct   = label === 'accept' ? allFields : label === 'reject' ? [] : allFields.filter(k => checked[k])
    const incorrect = label === 'reject'  ? allFields : label === 'accept' ? [] : allFields.filter(k => !checked[k])
    await api.post('/api/label', {
      input: lastInput,
      customer_id: customerId || null,
      context: { conversation_id: activeConv?.conversation_id },
      expected: buildExpected(),
      label,
      correct_fields: correct,
      incorrect_fields: incorrect,
    })
    setDone(label)
  }

  const urgencyClass = { high: 'b-red', normal: 'b-blue', low: 'b-green' }[cls.urgency] ?? 'b-gray'

  return (
    <>
      <div className="right-body">

        <div className="sec">
          <div className="sec-title">Intents</div>
          {intents.length === 0 && <span className="muted">none detected</span>}
          {intents.map(i => (
            <div key={i.name} className="intent-card">
              {partial && (
                <input type="checkbox" checked={!!checked['intent:' + i.name]}
                  onChange={e => toggle('intent:' + i.name, e.target.checked)} />
              )}
              <div className="intent-body">
                <div className="intent-name">{i.name}</div>
                <div className="intent-agent">→ {i.entities?.agent ?? '—'}</div>
                <div className="intent-conf">confidence {(i.confidence * 100).toFixed(0)}%</div>
                {i.reason && <div className="intent-reason" title={i.reason}>"{i.reason}"</div>}
              </div>
            </div>
          ))}
        </div>

        <div className="sec">
          <div className="sec-title">All intents — green = AI picked it</div>
          <div className="intent-grid">
            {allIntents.map(it => {
              const on = (cls.intents || []).includes(it.name)
              return (
                <span key={it.name} className={`chip ${on ? 'chip-on' : 'chip-off'}`}
                  title={`${it.name} → ${it.agent}`}>
                  {it.name}
                </span>
              )
            })}
          </div>
        </div>

        <div className="sec">
          <div className="sec-title">Agents Selected</div>
          <div className="agent-row">
            {partial && (
              <input type="checkbox" checked={!!checked['agents']}
                onChange={e => toggle('agents', e.target.checked)} />
            )}
            {(cls.agents ?? []).length === 0 && <span className="muted">none</span>}
            {(cls.agents ?? []).map(a => <span key={a} className="badge b-blue">{a}</span>)}
          </div>
        </div>

        <div className="sec">
          <div className="sec-title">Extracted Entities</div>
          <div className="field-row">
            {partial && (
              <input type="checkbox" checked={!!checked['entities']}
                onChange={e => toggle('entities', e.target.checked)} />
            )}
            <div>
              {entities.length === 0 && <span className="muted">none</span>}
              {entities.map(([k, v]) => (
                <div key={k} className="entity-row">
                  <span className="entity-key">{k}</span>
                  <span className="entity-val">{Array.isArray(v) ? v.join(', ') : String(v)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="sec flags-row">
          <div>
            <div className="sec-title">Human Needed</div>
            <div className="field-row">
              {partial && (
                <input type="checkbox" checked={!!checked['requires_human']}
                  onChange={e => toggle('requires_human', e.target.checked)} />
              )}
              <span className={`badge ${cls.requires_human ? 'b-red' : 'b-green'}`}>
                {cls.requires_human ? 'Yes' : 'No'}
              </span>
            </div>
          </div>
          <div>
            <div className="sec-title">Urgency</div>
            <div className="field-row">
              {partial && (
                <input type="checkbox" checked={!!checked['urgency']}
                  onChange={e => toggle('urgency', e.target.checked)} />
              )}
              <span className={`badge ${urgencyClass}`}>{cls.urgency}</span>
            </div>
          </div>
        </div>

        {cls.final_response && (
          <div className="sec">
            <div className="sec-title">System Response</div>
            <div className="final-resp">{cls.final_response}</div>
          </div>
        )}
      </div>

      <div className="label-section">
        <div className="label-title">Label this result</div>
        {done ? (
          <div className={`done-box done-${done}`}>
            {done === 'accept' ? '✓ Accepted' : done === 'reject' ? '✗ Rejected' : '~ Partially accepted'}
            {' — saved'}
          </div>
        ) : partial ? (
          <div>
            <div className="partial-hint">Check fields that are <strong>correct</strong>:</div>
            <button className="save-btn" onClick={() => submit('partial')}>Save Partial Label</button>
            <span className="cancel-link" onClick={() => { setPartial(false); setChecked({}) }}>cancel</span>
          </div>
        ) : (
          <div className="label-btns">
            <button className="lbtn lbtn-accept"  onClick={() => submit('accept')}>✓ Accept</button>
            <button className="lbtn lbtn-partial" onClick={() => setPartial(true)}>~ Partial</button>
            <button className="lbtn lbtn-reject"  onClick={() => submit('reject')}>✗ Reject</button>
          </div>
        )}
      </div>
    </>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [customers,  setCustomers]  = useState([])
  const [convs,      setConvs]      = useState([])
  const [activeConv, setActiveConv] = useState(null)
  const [messages,   setMessages]   = useState([])
  const [input,      setInput]      = useState('')
  const [classifier, setClassifier] = useState('llm')
  const [cls,        setCls]        = useState(null)
  const [customerId, setCustomerId] = useState('')
  const [loading,    setLoading]    = useState(false)
  const [lastInput,  setLastInput]  = useState('')
  const [allIntents, setAllIntents] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const bottomRef = useRef(null)

  const [leftTab,   setLeftTab]   = useState('threads') // 'threads' | 'approvals' | 'disputes'
  const [approvals, setApprovals] = useState([])
  const [disputes,  setDisputes]  = useState([])
  const [selApproval, setSelApproval] = useState(null)
  const [selDispute,  setSelDispute]  = useState(null)

  function refreshApprovals() {
    api.get('/api/approvals').then(rows => {
      setApprovals(rows)
      setSelApproval(cur => cur && rows.find(r => r.approval_id === cur.approval_id) || null)
    }).catch(() => {})
  }
  function refreshDisputes() {
    api.get('/api/disputes').then(rows => {
      setDisputes(rows)
      setSelDispute(cur => cur && rows.find(r => r.case_id === cur.case_id) || null)
    }).catch(() => {})
  }

  useEffect(() => {
    api.get('/api/customers').then(setCustomers).catch(() => {})
    api.get('/api/conversations').then(setConvs).catch(() => {})
    api.get('/api/intents').then(setAllIntents).catch(() => {})
    refreshApprovals()
    refreshDisputes()
    // ponytail: plain poll, not a websocket — an approval/dispute raised from
    // another channel still shows up within a few seconds without new infra.
    const id = setInterval(() => { refreshApprovals(); refreshDisputes() }, 15000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (!activeConv) return
    api.get(`/api/conversations/${activeConv.conversation_id}`).then(setMessages).catch(() => {})
    setCls(null)
    setSelectedId(null)
  }, [activeConv?.conversation_id])

  // Click a past inbound message to re-open how the LLM classified it.
  function openClassification(m) {
    const c = m.metadata?.classification
    if (!c) return
    setCls(c)
    setLastInput(m.text)
    setSelectedId(m.message_id)
    if (c.classifier) setClassifier(c.classifier)
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function newThread() {
    const conv = await api.post('/api/conversations', { customer_id: customerId || null })
    setConvs(prev => [conv, ...prev])
    setActiveConv(conv)
    setMessages([])
    setCls(null)
  }

  async function send() {
    if (!input.trim() || !activeConv || loading) return
    const text = input.trim()
    setInput('')
    setLastInput(text)
    setLoading(true)
    setCls(null)

    setMessages(prev => [...prev, {
      message_id: 'tmp-' + Date.now(),
      direction: 'inbound',
      text,
      timestamp: new Date().toISOString(),
    }])

    try {
      const result = await api.post('/api/classify', {
        message: text,
        customer_id: customerId || null,
        conversation_id: activeConv.conversation_id,
        classifier,
      })
      setCls(result)
      setSelectedId(result.message_id)
      api.get(`/api/conversations/${activeConv.conversation_id}`).then(setMessages)
      api.get('/api/conversations').then(setConvs)
      // A message can create an approval or dispute (sa3/sa4) — surface it right away.
      if ((result.agents || []).includes('sa4_approval')) refreshApprovals()
      if ((result.agents || []).includes('sa3_dispute')) refreshDisputes()
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const activeCustomer = customers.find(c => c.customer_id === customerId)

  return (
    <div className="layout">

      {/* ── LEFT ── */}
      <aside className="left">
        <div className="tab-bar">
          <button className={`tab-btn${leftTab === 'threads' ? ' active' : ''}`} onClick={() => setLeftTab('threads')}>
            Threads
          </button>
          <button className={`tab-btn${leftTab === 'approvals' ? ' active' : ''}`} onClick={() => setLeftTab('approvals')}>
            Approvals {approvals.length > 0 && <span className="tab-count">{approvals.length}</span>}
          </button>
          <button className={`tab-btn${leftTab === 'disputes' ? ' active' : ''}`} onClick={() => setLeftTab('disputes')}>
            Disputes {disputes.length > 0 && <span className="tab-count">{disputes.length}</span>}
          </button>
        </div>

        {leftTab === 'threads' && (
          <>
            <div className="left-header">
              <label>Customer ({customers.length})</label>
              <CustomerPicker customers={customers} value={customerId} onChange={setCustomerId} />
            </div>
            <button className="new-btn" onClick={newThread}>+ New Thread</button>
            <div className="conv-list">
              {convs.length === 0 && <div className="empty-list">No threads yet</div>}
              {convs.map(c => (
                <div key={c.conversation_id}
                  className={`conv-item${activeConv?.conversation_id === c.conversation_id ? ' active' : ''}`}
                  onClick={() => setActiveConv(c)}>
                  <div className="conv-id">…{c.conversation_id?.slice(-10)}</div>
                  <div className="conv-meta">{c.status} · {fmt(c.updated_at)}</div>
                </div>
              ))}
            </div>
          </>
        )}

        {leftTab === 'approvals' && (
          <div className="conv-list">
            {approvals.length === 0 && <div className="empty-list">No pending approvals</div>}
            {approvals.map(a => (
              <div key={a.approval_id}
                className={`queue-item${selApproval?.approval_id === a.approval_id ? ' active' : ''}`}
                onClick={() => setSelApproval(a)}>
                <div className="queue-title">{APPROVAL_TYPE_LABEL[a.type] ?? a.type}</div>
                <div className="queue-meta">
                  <span>{a.customer_name}</span>
                  {inr(a.amount) && <span>{inr(a.amount)}</span>}
                  <span className={`badge ${a.status === 'pending' ? 'b-yellow' : a.status === 'approved' ? 'b-green' : 'b-red'}`}>{a.status}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {leftTab === 'disputes' && (
          <div className="conv-list">
            {disputes.length === 0 && <div className="empty-list">No open disputes</div>}
            {disputes.map(c => (
              <div key={c.case_id}
                className={`queue-item${selDispute?.case_id === c.case_id ? ' active' : ''}`}
                onClick={() => setSelDispute(c)}>
                <div className="queue-title">{c.title}</div>
                <div className="queue-meta">
                  <span>{c.customer_name}</span>
                  <span className={`badge ${c.priority === 'high' || c.priority === 'critical' ? 'b-red' : 'b-blue'}`}>{c.priority}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </aside>

      {/* ── CENTER ── */}
      <main className="center">
        <div className="center-header">
          {leftTab === 'approvals' ? 'Approvals' : leftTab === 'disputes' ? 'Disputes' : (
            activeConv ? (
              <>
                <span className="conv-ref">{activeConv.conversation_id}</span>
                {activeCustomer && <span className="cust-name">· {activeCustomer.display_name}</span>}
              </>
            ) : 'Select or create a thread'
          )}
        </div>

        {leftTab === 'approvals' ? (
          <ApprovalDetail approval={selApproval} onDecided={refreshApprovals} />
        ) : leftTab === 'disputes' ? (
          <DisputeDetail case_={selDispute} onResolved={refreshDisputes} />
        ) : !activeConv ? (
          <div className="no-thread">← Select a thread or click + New Thread</div>
        ) : (
          <>
            <div className="messages">
              {messages.map(m => {
                const clickable = !!m.metadata?.classification
                return (
                  <div key={m.message_id}
                    className={`msg msg-${m.direction === 'inbound' ? 'in' : 'out'}`
                      + (clickable ? ' msg-clickable' : '')
                      + (selectedId === m.message_id ? ' msg-selected' : '')}
                    onClick={() => openClassification(m)}>
                    {m.text}
                    <div className="ts">
                      {fmt(m.timestamp)}
                      {clickable && <span className="msg-hint"> · click to inspect</span>}
                    </div>
                  </div>
                )
              })}
              {loading && <div className="thinking">Classifying…</div>}
              <div ref={bottomRef} />
            </div>

            <div className="input-area">
              <textarea
                rows={3}
                placeholder="Type a customer message… (Enter to send, Shift+Enter for newline)"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={onKey}
                disabled={loading}
              />
              <div className="controls">
                <div className="clf-toggle">
                  <button className={`clf-btn${classifier === 'llm'   ? ' clf-on' : ''}`} onClick={() => setClassifier('llm')}>LLM</button>
                  <button className={`clf-btn${classifier === 'rules' ? ' clf-on' : ''}`} onClick={() => setClassifier('rules')}>Rules</button>
                </div>
                <button className="send-btn" onClick={send} disabled={loading || !input.trim()}>Send</button>
              </div>
            </div>
          </>
        )}
      </main>

      {/* ── RIGHT ── */}
      <aside className="right">
        <div className="right-header">
          Classification
          {cls && <span className="clf-badge">{classifier.toUpperCase()}</span>}
        </div>
        <RightPanel
          cls={cls}
          classifier={classifier}
          activeConv={activeConv}
          lastInput={lastInput}
          customerId={customerId}
          allIntents={allIntents}
        />
      </aside>

    </div>
  )
}

// ── Searchable customer picker ──────────────────────────────────────────────────

function CustomerPicker({ customers, value, onChange }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const selected = customers.find(c => c.customer_id === value)

  const q = query.trim().toLowerCase()
  const matches = (q ? customers.filter(c => c.display_name.toLowerCase().includes(q)) : customers).slice(0, 100)

  function pick(c) {
    onChange(c ? c.customer_id : '')
    setQuery('')
    setOpen(false)
  }

  return (
    <div className="picker">
      <input
        className="picker-input"
        placeholder="— no customer — (type to search)"
        value={open ? query : (selected ? selected.display_name : '')}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && (
        <div className="picker-list">
          <div className="picker-opt" onMouseDown={() => pick(null)}>— no customer —</div>
          {matches.map(c => (
            <div key={c.customer_id} className="picker-opt" onMouseDown={() => pick(c)}>
              {c.display_name}
            </div>
          ))}
          {matches.length === 0 && <div className="picker-empty">no match</div>}
        </div>
      )}
    </div>
  )
}
