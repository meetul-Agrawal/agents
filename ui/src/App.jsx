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

  useEffect(() => {
    api.get('/api/customers').then(setCustomers).catch(() => {})
    api.get('/api/conversations').then(setConvs).catch(() => {})
    api.get('/api/intents').then(setAllIntents).catch(() => {})
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
      </aside>

      {/* ── CENTER ── */}
      <main className="center">
        <div className="center-header">
          {activeConv ? (
            <>
              <span className="conv-ref">{activeConv.conversation_id}</span>
              {activeCustomer && <span className="cust-name">· {activeCustomer.display_name}</span>}
            </>
          ) : 'Select or create a thread'}
        </div>

        {!activeConv ? (
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
