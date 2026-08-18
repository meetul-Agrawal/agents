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
  call_schedule: 'Call schedule',
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
        outcome, resolution: note, note,
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
                {e.type === 'invoice_on_record' && (
                  <>
                    Invoice {e.voucher_number}: {inr(e.amount)} on {e.date}.
                    {(e.items || []).length === 1 && <> Item: {e.items[0]}.</>}
                    {(e.items || []).length > 1 && (e.matched_items || []).length === 1 && (
                      <> Item: <strong>{e.matched_items[0]}</strong> (of {e.items.length} on this invoice).</>
                    )}
                    {(e.items || []).length > 1 && (e.matched_items || []).length !== 1 && (
                      <> Multiple items on this invoice — unclear which: {e.items.join(', ')}.</>
                    )}
                  </>
                )}
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

// ── Promise detail — view / update status ──────────────────────────────────

function PromiseDetail({ promise, onUpdated }) {
  const [busy, setBusy] = useState(false)
  const [paidAmt, setPaidAmt] = useState('')
  const [note, setNote] = useState('')
  const [updatedMsg, setUpdatedMsg] = useState(null)

  useEffect(() => {
    setPaidAmt(promise?.paid_amount ? String(promise.paid_amount) : '')
    setNote('')
    setUpdatedMsg(null)
  }, [promise?.promise_id])

  if (!promise) return <div className="no-thread">← Pick a payment promise</div>

  async function updateStatus(newStatus) {
    setBusy(true)
    try {
      const payload = { status: newStatus, note }
      if (newStatus === 'paid') {
        payload.paid_amount = promise.amount
      } else if (newStatus === 'partial' && paidAmt) {
        payload.paid_amount = parseFloat(paidAmt)
      }
      const res = await api.post(`/api/promises/${promise.promise_id}/status`, payload)
      if (res.ok) {
        setUpdatedMsg(`Status updated to ${newStatus}`)
        onUpdated()
      }
    } finally {
      setBusy(false)
    }
  }

  const isPromised = promise.status === 'promised'

  return (
    <div className="detail-view">
      <div className="detail-title">Payment Promise · {inr(promise.amount)}</div>
      <div className="detail-sub">
        {promise.customer_name} · {promise.promise_id}
        {' · '}Due date: <strong>{promise.due_date}</strong>
        {' · '}<span className={`badge ${
          promise.status === 'promised' ? 'b-yellow' : promise.status === 'paid' ? 'b-green' : promise.status === 'missed' ? 'b-red' : promise.status === 'partial' ? 'b-blue' : 'b-gray'
        }`}>{promise.status}</span>
      </div>

      <div className="sec">
        <div className="sec-title">Promise Details</div>
        <ul className="detail-list">
          <li><strong>Promised Amount:</strong> {inr(promise.amount)}</li>
          <li><strong>Paid Amount:</strong> {inr(promise.paid_amount || 0)}</li>
          <li><strong>Due Date:</strong> {promise.due_date}</li>
          <li><strong>Created:</strong> {fmt(promise.created_at)}</li>
          <li><strong>Last Updated:</strong> {fmt(promise.updated_at)}</li>
          {promise.conversation_id && <li><strong>Originating Thread:</strong> {promise.conversation_id}</li>}
        </ul>
      </div>

      {isPromised && (
        <div className="sec">
          <div className="sec-title">Update Status</div>
          <div style={{ display: 'flex', gap: '8px', marginBottom: '8px', alignItems: 'center' }}>
            <label style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Partial Amount (if partial):</label>
            <input
              type="number"
              placeholder="e.g. 50000"
              value={paidAmt}
              onChange={e => setPaidAmt(e.target.value)}
              style={{
                background: 'var(--surface)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                padding: '4px 8px',
                fontSize: '12px',
                width: '120px',
              }}
            />
          </div>
          <textarea
            className="detail-note"
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder="Optional internal note regarding this promise…"
            disabled={busy}
          />
          <div className="detail-actions">
            <button className="dbtn dbtn-approve" disabled={busy} onClick={() => updateStatus('paid')}>✓ Mark as Paid</button>
            <button className="dbtn" style={{ background: 'var(--badge-blue-bg)', color: 'var(--badge-blue-fg)' }} disabled={busy} onClick={() => updateStatus('partial')}>~ Partial Paid</button>
            <button className="dbtn dbtn-reject" disabled={busy} onClick={() => updateStatus('missed')}>✗ Mark Missed</button>
            <button className="dbtn dbtn-drop" disabled={busy} onClick={() => updateStatus('cancelled')}>Cancel Promise</button>
          </div>
        </div>
      )}

      {updatedMsg && (
        <div className="sent-box">
          <div className="sent-label">Updated</div>
          {updatedMsg}
        </div>
      )}
    </div>
  )
}

// ── Call Prep Modal ──────────────────────────────────────────────────────────

function CallPrepModal({ data, loading, onClose, onRefresh }) {
  const [scriptLang, setScriptLang] = useState('hinglish') // 'hinglish' | 'english'
  const [copied, setCopied] = useState(false)

  if (!data && loading) {
    return (
      <div className="modal-backdrop" onClick={onClose}>
        <div className="modal-content" onClick={e => e.stopPropagation()} style={{ maxWidth: '440px', textAlign: 'center', padding: '36px 24px' }}>
          <div style={{ fontSize: '24px', marginBottom: '12px' }}>📞</div>
          <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text)', marginBottom: '6px' }}>
            Analyzing MongoDB Records & Chat Context…
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Building personalized talking points and dialogue scripts…
          </div>
        </div>
      </div>
    )
  }

  if (!data) return null

  function copyScript() {
    const text = scriptLang === 'hinglish' ? data.call_script_hinglish : data.call_script_english
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-row">
            <span style={{ fontSize: '22px' }}>📞</span>
            <div>
              <div className="modal-title">Call Prep Brief · {data.customer_name}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                Customer ID: {data.customer_id} · Grounded in live MongoDB books & conversation history
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <button className="copy-btn" onClick={onRefresh} disabled={loading} title="Re-generate with fresh context">
              {loading ? '↻ Loading…' : '↻ Refresh'}
            </button>
            <button className="modal-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="modal-body">
          {/* Account Overview Cards */}
          <div className="prep-stat-grid">
            <div className="prep-stat-card">
              <div className="prep-stat-lbl">Total Outstanding</div>
              <div className="prep-stat-val" style={{ color: '#f87171' }}>{data.total_outstanding_formatted}</div>
              <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '2px' }}>{data.open_bills_count} open bill(s)</div>
            </div>
            <div className="prep-stat-card">
              <div className="prep-stat-lbl">Ageing Breakdown</div>
              <div className="prep-stat-val" style={{ fontSize: '12px' }}>{data.ageing_summary}</div>
            </div>
            <div className="prep-stat-card">
              <div className="prep-stat-lbl">Payment Track Record</div>
              <div className="prep-stat-val" style={{ fontSize: '12px' }}>{data.payment_behaviour_summary}</div>
            </div>
            <div className="prep-stat-card">
              <div className="prep-stat-lbl">Active Promises / Disputes</div>
              <div className="prep-stat-val" style={{ fontSize: '12px' }}>
                {data.active_promise_summary} · {data.open_dispute_summary}
              </div>
            </div>
          </div>

          {/* Account Summary & Chat Context */}
          <div className="sec">
            <div className="sec-title">Account Summary & Chat Context</div>
            <div className="final-resp" style={{ marginBottom: '6px' }}>
              <strong>Summary:</strong> {data.account_summary}
            </div>
            {data.recent_chat_summary && (
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', background: 'var(--page-bg)', padding: '8px 10px', borderRadius: '6px', border: '1px solid var(--border-light)' }}>
                <strong>Recent Thread Context:</strong> {data.recent_chat_summary}
              </div>
            )}
          </div>

          {/* Key Talking Points */}
          <div className="sec">
            <div className="sec-title">Key Talking Points (Checklist for Caller)</div>
            {data.talking_points?.map((tp, idx) => (
              <div key={idx} className={`talking-point-item priority-${tp.priority || 'medium'}`}>
                <div className="talking-point-title">
                  <span className={`badge ${tp.priority === 'high' ? 'b-red' : tp.priority === 'medium' ? 'b-yellow' : 'b-blue'}`}>
                    {tp.category || 'Topic'}
                  </span>
                  <span>{tp.point}</span>
                </div>
                <div className="talking-point-detail">{tp.detail}</div>
              </div>
            ))}
          </div>

          {/* Call Script Section */}
          <div className="sec">
            <div className="script-tab-row">
              <div className="sec-title" style={{ margin: 0 }}>Call Dialogue Script</div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <div className="script-tabs">
                  <button
                    className={`script-tab-btn${scriptLang === 'hinglish' ? ' active' : ''}`}
                    onClick={() => setScriptLang('hinglish')}
                  >
                    Hindi / Hinglish
                  </button>
                  <button
                    className={`script-tab-btn${scriptLang === 'english' ? ' active' : ''}`}
                    onClick={() => setScriptLang('english')}
                  >
                    English
                  </button>
                </div>
                <button className="copy-btn" onClick={copyScript}>
                  {copied ? '✓ Copied!' : '📋 Copy Script'}
                </button>
              </div>
            </div>
            <div className="script-box">
              {scriptLang === 'hinglish' ? data.call_script_hinglish : data.call_script_english}
            </div>
          </div>

          {/* Objection Handling */}
          {data.objection_handling && data.objection_handling.length > 0 && (
            <div className="sec">
              <div className="sec-title">Anticipated Objections & Response Strategy</div>
              {data.objection_handling.map((obj, idx) => (
                <div key={idx} className="objection-card">
                  <div className="obj-q">❓ Customer Objection: "{obj.likely_objection}"</div>
                  <div className="obj-a">💡 <strong>Tactical Response:</strong> {obj.recommended_response}</div>
                </div>
              ))}
            </div>
          )}

          {/* Target Commitment & Notes */}
          <div className="sec flags-row">
            <div style={{ flex: 1 }}>
              <div className="sec-title">Recommended Target Commitment</div>
              <div className="final-resp" style={{ color: 'var(--accent)', fontWeight: 600 }}>
                🎯 {data.recommended_target_commitment}
              </div>
            </div>
            {data.notes_for_agent && data.notes_for_agent.length > 0 && (
              <div style={{ flex: 1 }}>
                <div className="sec-title">Caller Behavioral Notes</div>
                <ul className="detail-list">
                  {data.notes_for_agent.map((n, i) => <li key={i}>• {n}</li>)}
                </ul>
              </div>
            )}
          </div>

        </div>
      </div>
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
  const [loading,    setLoading]    = useState(false)
  const [lastInput,  setLastInput]  = useState('')
  const [allIntents, setAllIntents] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const bottomRef = useRef(null)

  const [newThreadCustId, setNewThreadCustId] = useState('')

  // Call prep state
  const [callPrep, setCallPrep] = useState(null)
  const [callPrepLoading, setCallPrepLoading] = useState(false)
  const [showCallPrepModal, setShowCallPrepModal] = useState(false)

  const [showVoucherModal, setShowVoucherModal] = useState(false)

  const [leftTab,   setLeftTab]   = useState('threads') // 'threads' | 'approvals' | 'disputes' | 'promises'
  const [approvals, setApprovals] = useState([])
  const [disputes,  setDisputes]  = useState([])
  const [promises,  setPromises]  = useState([])
  const [selApproval, setSelApproval] = useState(null)
  const [selDispute,  setSelDispute]  = useState(null)
  const [selPromise,  setSelPromise]  = useState(null)

  // The active conversation's customer_id — read from the conversation object, not editable.
  const customerId = activeConv?.customer_id || ''

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
  function refreshPromises() {
    api.get('/api/promises').then(rows => {
      setPromises(rows)
      setSelPromise(cur => cur && rows.find(r => r.promise_id === cur.promise_id) || null)
    }).catch(() => {})
  }
  // A decision/resolution sends its reply straight into a conversation, not
  // necessarily the one open right now — refetch so it shows without the
  // customer having to click away and back.
  function refreshMessages() {
    if (!activeConv) return
    api.get(`/api/conversations/${activeConv.conversation_id}`).then(setMessages).catch(() => {})
  }

  useEffect(() => {
    api.get('/api/customers').then(setCustomers).catch(() => {})
    api.get('/api/conversations').then(setConvs).catch(() => {})
    api.get('/api/intents').then(setAllIntents).catch(() => {})
    refreshApprovals()
    refreshDisputes()
    refreshPromises()
    // ponytail: plain poll, not a websocket — an approval/dispute/promise raised from
    // another channel still shows up within a few seconds without new infra.
    const id = setInterval(() => { refreshApprovals(); refreshDisputes(); refreshPromises() }, 15000)
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
    if (!newThreadCustId) return  // require customer selection
    const conv = await api.post('/api/conversations', { customer_id: newThreadCustId })
    setConvs(prev => [conv, ...prev])
    setActiveConv(conv)
    setMessages([])
    setCls(null)
    setNewThreadCustId('')  // reset picker for next creation
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
      // A message can create an approval, dispute, or payment promise (sa3/sa4/sa2) — surface it right away.
      if ((result.agents || []).includes('sa4_approval')) refreshApprovals()
      if ((result.agents || []).includes('sa3_dispute')) refreshDisputes()
      if ((result.agents || []).includes('sa2_recovery')) refreshPromises()
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  // Look up display name for the active conversation's customer
  const activeCustomer = customers.find(c => c.customer_id === customerId)

  async function handleOpenCallPrep() {
    if (!customerId) return
    setCallPrepLoading(true)
    setShowCallPrepModal(true)
    try {
      const convParam = activeConv?.conversation_id ? `?conversation_id=${activeConv.conversation_id}` : ''
      const res = await api.get(`/api/customers/${customerId}/call-prep${convParam}`)
      setCallPrep(res)
    } catch (e) {
      console.error(e)
    } finally {
      setCallPrepLoading(false)
    }
  }

  // Helper: look up customer display name by id (for thread list)
  function custName(cid) {
    if (!cid) return null
    const c = customers.find(c => c.customer_id === cid)
    return c ? c.display_name : '…' + cid.slice(-8)
  }

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
          <button className={`tab-btn${leftTab === 'promises' ? ' active' : ''}`} onClick={() => setLeftTab('promises')}>
            Promises {promises.filter(p => p.status === 'promised').length > 0 && <span className="tab-count">{promises.filter(p => p.status === 'promised').length}</span>}
          </button>
        </div>

        {leftTab === 'threads' && (
          <>
            <div className="left-header">
              <label>New thread — select customer</label>
              <CustomerPicker customers={customers} value={newThreadCustId} onChange={setNewThreadCustId} />
            </div>
            <button
              className="new-btn"
              onClick={newThread}
              disabled={!newThreadCustId}
              title={newThreadCustId ? 'Create thread' : 'Select a customer first'}
            >+ New Thread</button>
            <div className="conv-list">
              {convs.length === 0 && <div className="empty-list">No threads yet</div>}
              {convs.map(c => (
                <div key={c.conversation_id}
                  className={`conv-item${activeConv?.conversation_id === c.conversation_id ? ' active' : ''}`}
                  onClick={() => setActiveConv(c)}>
                  <div className="conv-id">
                    {custName(c.customer_id)
                      ? <span className="conv-cust">{custName(c.customer_id)}</span>
                      : <span className="muted">no customer</span>}
                  </div>
                  <div className="conv-meta">…{c.conversation_id?.slice(-10)} · {c.status} · {fmt(c.updated_at)}</div>
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

        {leftTab === 'promises' && (
          <div className="conv-list">
            {promises.length === 0 && <div className="empty-list">No payment promises</div>}
            {promises.map(p => (
              <div key={p.promise_id}
                className={`queue-item${selPromise?.promise_id === p.promise_id ? ' active' : ''}`}
                onClick={() => setSelPromise(p)}>
                <div className="queue-title">{inr(p.amount)}</div>
                <div className="queue-meta">
                  <span>{p.customer_name}</span>
                  <span>Due: {p.due_date}</span>
                  <span className={`badge ${
                    p.status === 'promised' ? 'b-yellow' : p.status === 'paid' ? 'b-green' : p.status === 'missed' ? 'b-red' : p.status === 'partial' ? 'b-blue' : 'b-gray'
                  }`}>{p.status}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <button className="new-btn" onClick={() => setShowVoucherModal(true)}>
          🗂️ View Vouchers (MongoDB)
        </button>
      </aside>

      {/* ── CENTER ── */}
      <main className="center">
        <div className="center-header">
          {leftTab === 'approvals' ? 'Approvals' : leftTab === 'disputes' ? 'Disputes' : leftTab === 'promises' ? 'Payment Promises' : (
            activeConv ? (
              <>
                <span className="conv-ref">{activeConv.conversation_id}</span>
                {activeCustomer && <span className="cust-name">· {activeCustomer.display_name}</span>}
              </>
            ) : 'Select or create a thread'
          )}
        </div>

        {leftTab === 'approvals' ? (
          <ApprovalDetail approval={selApproval} onDecided={() => { refreshApprovals(); refreshMessages() }} />
        ) : leftTab === 'disputes' ? (
          <DisputeDetail case_={selDispute} onResolved={() => { refreshDisputes(); refreshMessages() }} />
        ) : leftTab === 'promises' ? (
          <PromiseDetail promise={selPromise} onUpdated={() => { refreshPromises(); refreshMessages() }} />
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
                <button
                  className="call-prep-btn"
                  onClick={handleOpenCallPrep}
                  disabled={!customerId || callPrepLoading}
                  title={customerId ? "Generate Call Brief & Talking Script for this customer" : "Select a thread with a customer first"}
                >
                  {callPrepLoading ? '⏳ Prep…' : '📞 Call Prep'}
                </button>
                <div className="clf-toggle">
                  <button className="clf-btn clf-on">LLM</button>
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

      {showCallPrepModal && (
        <CallPrepModal
          data={callPrep}
          loading={callPrepLoading}
          onClose={() => setShowCallPrepModal(false)}
          onRefresh={handleOpenCallPrep}
        />
      )}

      {showVoucherModal && (
        <VoucherModal customers={customers} onClose={() => setShowVoucherModal(false)} />
      )}

    </div>
  )
}

// ── Voucher browser modal ────────────────────────────────────────────────────

const VOUCHER_CATS = ['all', 'Sales', 'Receipt', 'Credit Note']

function VoucherModal({ customers, onClose }) {
  const [custId, setCustId] = useState('')
  const [category, setCategory] = useState('all')
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!custId) { setRows([]); return }
    setLoading(true)
    api.get(`/api/customers/${custId}/vouchers?category=${encodeURIComponent(category)}`)
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [custId, category])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content voucher-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-row">
            <span style={{ fontSize: '22px' }}>🗂️</span>
            <div className="modal-title">Voucher Browser · MongoDB</div>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          <div className="voucher-toolbar">
            <div style={{ width: '320px' }}>
              <CustomerPicker customers={customers} value={custId} onChange={setCustId} />
            </div>
            <div className="script-tabs">
              {VOUCHER_CATS.map(c => (
                <button key={c} className={`script-tab-btn${category === c ? ' active' : ''}`}
                  onClick={() => setCategory(c)}>
                  {c === 'all' ? 'All' : c === 'Receipt' ? 'Receipts' : c}
                </button>
              ))}
            </div>
          </div>

          {!custId && <div className="no-thread">← Select a customer to see its vouchers</div>}
          {custId && loading && <div className="thinking">Loading vouchers…</div>}
          {custId && !loading && rows.length === 0 && <div className="empty-list">No vouchers for this filter</div>}
          {custId && !loading && rows.length > 0 && (
            <div className="voucher-table-wrap">
              <table className="voucher-table">
                <thead>
                  <tr><th>Date</th><th>Voucher #</th><th>Type</th><th>Category</th><th>Amount</th><th>Items</th></tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>
                      <td>{r.date}</td>
                      <td>{r.voucher_number}</td>
                      <td>{r.voucher_type}</td>
                      <td>{r.category}</td>
                      <td>{inr(r.amount)}</td>
                      <td>{(r.items || []).map(it => it.name).filter(Boolean).join(', ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
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
