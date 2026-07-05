import { useState, useEffect, useRef } from 'react'
import { api, fmtSize, fmtNum } from '../api.js'
import { DiskBadge, DiskBar } from './PathInfo.jsx'


function PathSelector({ value, onChange, history }) {
  const [validity, setValidity] = useState(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    if (!value) { setValidity(null); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      api.validatePath(value).then(setValidity).catch(() => setValidity(null))
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [value])

  // F6 — uniquement les chemins déjà utilisés (paths_history.json), source ET
  // cible, dédoublonnés. On n'ajoute PAS les disques/réseau découverts : le
  // déroulant ne propose QUE les chemins présents dans l'historique.
  const uniqueSuggestions = [...new Set([
    ...history.map(h => h.source).filter(Boolean),
    ...history.map(h => h.target).filter(Boolean),
  ])]

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:8, flexWrap:'wrap' }}>
        <label style={{ margin:0 }}>Dossier à analyser</label>
        <DiskBadge disk={validity?.disk} validity={validity} />
      </div>

      {/* F6 — menu déroulant explicite des chemins connus (historique source+cible
          + disques découverts). Choisir une entrée remplit le champ ci-dessous ;
          la saisie libre reste possible. Remplace le datalist (qui ne s'ouvrait
          pas tout seul selon le navigateur). */}
      {uniqueSuggestions.length > 0 && (
        <select value="" onChange={e => { if (e.target.value) onChange(e.target.value) }}
          style={{ width:'100%', padding:'8px 10px', fontSize:12, fontFamily:'var(--mono)',
                   color:'var(--text)', background:'var(--bg)', border:'1px solid var(--border)',
                   borderRadius:'var(--radius)', cursor:'pointer' }}>
          <option value="">📂 Chemins connus… ({uniqueSuggestions.length})</option>
          {uniqueSuggestions.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      )}

      <input
        type="text" value={value} list="cleanup-paths"
        onChange={e => onChange(e.target.value)}
        placeholder="/disks/… ou /network/… (ou choisis ci-dessus)"
        style={{ minWidth:0 }} />
      <DiskBar disk={validity?.disk} />
      <datalist id="cleanup-paths">
        {uniqueSuggestions.map(p => <option key={p} value={p} />)}
      </datalist>
    </div>
  )
}


function PlanTable({ plan, filter, setFilter }) {
  const filtered = plan.candidates.filter(c => {
    if (filter === 'deletable') return !c.protected
    if (filter === 'protected') return c.protected
    return true
  })

  return (
    <div>
      <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:10 }}>
        {[
          { id:'all',       label:`Tous (${plan.total})` },
          { id:'deletable', label:`🟢 Supprimables (${plan.deletable})`, color:'var(--success)' },
          { id:'protected', label:`🔴 Protégés (${plan.protected})`,     color:'var(--danger)' },
        ].map(f => (
          <button key={f.id} onClick={() => setFilter(f.id)} style={{
            padding:'5px 10px', fontSize:12, borderRadius:4,
            background: filter === f.id
              ? (f.color ? f.color + '33' : 'var(--accent)')
              : 'var(--bg)',
            color: filter === f.id ? (f.color || '#fff') : 'var(--muted)',
            border: `1px solid ${filter === f.id ? (f.color || 'var(--accent)') : 'var(--border)'}`,
            textTransform:'none', letterSpacing:0, cursor:'pointer',
          }}>{f.label}</button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div style={{ color:'var(--muted)', fontSize:12, padding:12,
                       background:'var(--bg)', borderRadius:6 }}>
          Aucun résultat pour ce filtre.
        </div>
      ) : (
        <div style={{ maxHeight:420, overflowY:'auto', overflowX:'auto',
                       border:'1px solid var(--border)', borderRadius:6 }}>
          <table style={{ width:'100%', fontSize:12, borderCollapse:'collapse', minWidth:540 }}>
            <thead style={{ position:'sticky', top:0, background:'var(--surface)' }}>
              <tr>
                <th style={th}>Statut</th>
                <th style={th}>Chemin</th>
                <th style={{...th, textAlign:'right'}}>Taille</th>
                <th style={th}>Raison</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 1000).map((c, i) => (
                <tr key={i} style={{ borderTop:'1px solid var(--border)' }}>
                  <td style={{...td, whiteSpace:'nowrap', width:90}}>
                    {c.protected
                      ? <span style={{ color:'var(--danger)', fontWeight:600, fontSize:10, letterSpacing:'.05em' }}>🔴 PROTÉGÉ</span>
                      : <span style={{ color:'var(--success)', fontWeight:600, fontSize:10, letterSpacing:'.05em' }}>🟢 À SUPPRIMER</span>}
                  </td>
                  <td style={{...td, fontFamily:'var(--mono)', wordBreak:'break-all'}}>
                    {c.relative_path}
                  </td>
                  <td style={{...td, textAlign:'right', fontFamily:'var(--mono)', color:'var(--muted)', width:80}}>
                    {fmtSize(c.size)}
                  </td>
                  <td style={{...td, color:'var(--muted)', fontSize:11}}>
                    {c.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > 1000 && (
            <div style={{ padding:10, color:'var(--muted)', fontSize:11, textAlign:'center' }}>
              … {filtered.length - 1000} entrée(s) supplémentaires non affichées
            </div>
          )}
        </div>
      )}
    </div>
  )
}


export default function TabCleanup({ status }) {
  const [paths,   setPaths]   = useState({ disks:[], network:[] })
  const [history, setHistory] = useState([])
  const [root,    setRoot]    = useState('')
  const [plan,    setPlan]    = useState(null)
  const [filter,  setFilter]  = useState('deletable')
  const [dryRun,  setDryRun]  = useState(true)
  const [force,   setForce]   = useState(false)  // v3.10 : ignorer la protection audio
  const [busy,    setBusy]    = useState(false)
  const [msg,     setMsg]     = useState(null)

  useEffect(() => {
    api.discover().then(setPaths).catch(() => {})
    api.pathsHistory().then(setHistory).catch(() => {})
    api.cleanPlan().then(setPlan).catch(() => setPlan(null))
  }, [])

  // Rafraîchir le plan dès que le scan se termine
  const prev = useRef(status?.app_state)
  useEffect(() => {
    if (prev.current && prev.current !== 'IDLE' && status?.app_state === 'IDLE'
        && status?.method === 'cleanup_db') {
      api.cleanPlan().then(setPlan).catch(() => {})
    }
    prev.current = status?.app_state
  }, [status?.app_state, status?.method])

  const pollRef = useRef(null)
  function refreshPlanWhenDone() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    let n = 0
    pollRef.current = setInterval(async () => {
      n += 1
      let st = 'IDLE'
      try { st = (await (await fetch('/api/status')).json()).app_state } catch (e) { return }
      if (!['SCANNING', 'COMPARING', 'SYNCING', 'VERIFYING'].includes(st) || n > 4000) {
        clearInterval(pollRef.current); pollRef.current = null
        api.cleanPlan().then(setPlan).catch(() => {})
      }
    }, 1200)
  }
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const isActive = status && !['IDLE', 'ERROR'].includes(status.app_state)
  const isCleanupOp = status?.method === 'cleanup_db'
  const notify = (text, ok=true) => { setMsg({ text, ok }); setTimeout(() => setMsg(null), 5000) }

  async function doScan() {
    if (!root) return notify('Choisis un dossier à analyser', false)
    if (force && !confirm(
      "⚠ MODE FORCE activé.\n\n" +
      "La protection audio sera IGNORÉE : tous les fichiers .db trouvés " +
      "seront marqués comme supprimables, même ceux dans des dossiers " +
      "contenant de la musique (.flac, .mp3, .m4a).\n\n" +
      "Tu pourras toujours vérifier la liste avant la suppression effective.\n\n" +
      "Continuer le scan ?"
    )) return
    try {
      setBusy(true)
      await api.cleanScan({ root, force })
      refreshPlanWhenDone()
      notify(force
        ? '⚠ Scan FORCE démarré — protection audio désactivée'
        : 'Scan démarré — patiente jusqu\'à la fin avant d\'examiner le plan')
    } catch (e) { notify(e.message, false) }
    finally { setBusy(false) }
  }

  async function doExecute() {
    if (!plan) return notify('Lance d\'abord un scan', false)
    if (plan.deletable === 0) return notify('Rien à supprimer', false)

    const isPlanForced = plan.force === true
    const forceWarning = isPlanForced
      ? "\n\n⚠ Ce plan a été généré en MODE FORCE : certains .db marqués supprimables " +
        "se trouvent dans des dossiers contenant de la musique."
      : ""

    const confirmMsg = dryRun
      ? `SIMULER la suppression de ${plan.deletable} fichier(s) .db (${fmtSize(plan.deletable_size)}) ?\n\nAucune écriture ne sera effectuée.${forceWarning}`
      : `⚠ SUPPRIMER DÉFINITIVEMENT ${plan.deletable} fichier(s) .db (${fmtSize(plan.deletable_size)}) ?\n\nCette action est IRRÉVERSIBLE.${isPlanForced
          ? "\nLA PROTECTION AUDIO EST DÉSACTIVÉE — des .db dans des dossiers de musique seront supprimés."
          : "\nLes fichiers protégés (dossiers contenant de l'audio) ne seront PAS touchés."}`

    if (!confirm(confirmMsg)) return
    try {
      setBusy(true)
      await api.cleanExecute({ root, dry_run: dryRun })
      refreshPlanWhenDone()
      notify(dryRun ? 'Simulation démarrée' : 'Suppression démarrée')
    } catch (e) { notify(e.message, false) }
    finally { setBusy(false) }
  }

  async function doAbort() {
    try { await api.abort(); notify('Arrêt demandé') }
    catch (e) { notify(e.message, false) }
  }

  // On charge un nouveau plan automatiquement quand le root change (si root correspond)
  useEffect(() => {
    if (plan?.root && !root) setRoot(plan.root)
  }, [plan])

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>

      {msg && (
        <div style={{
          padding:'10px 16px', borderRadius:'var(--radius)',
          background: msg.ok ? '#14532d' : '#450a0a',
          color: msg.ok ? 'var(--success)' : 'var(--danger)',
        }}>{msg.text}</div>
      )}

      {/* Avertissement règle */}
      <div className="card" style={{ padding:'12px 16px', borderLeft:'3px solid var(--warning)',
                                       background:'#1c1917' }}>
        <div style={{ fontSize:13, color:'var(--text)', marginBottom:6 }}>
          <strong>🧹 Nettoyage des fichiers .db</strong>
        </div>
        <div style={{ fontSize:12, color:'var(--muted)', lineHeight:1.5 }}>
          Cet outil supprime uniquement les fichiers <code>.db</code> du dossier
          choisi (source <strong>ou</strong> cible).
          <br /><strong style={{ color:'var(--warning)' }}>Protection automatique :</strong>{' '}
          si le dossier parent d'un <code>.db</code> contient (récursivement)
          des fichiers audio <code>.flac</code>, <code>.mp3</code> ou <code>.m4a</code>,
          le <code>.db</code> est marqué <strong>🔴 PROTÉGÉ</strong> et ne sera pas supprimé.
          <br />C'est l'unique exception à la règle « source = lecture seule ».
        </div>
      </div>

      {/* Sélection du dossier */}
      <div className="card">
        <h3 style={{ marginBottom:12, fontSize:14 }}>Étape 1 — Analyser un dossier</h3>
        <div style={{ display:'grid',
                       gridTemplateColumns:'repeat(auto-fit, minmax(260px, 1fr))', gap:16,
                       alignItems:'end' }}>
          <PathSelector value={root} onChange={setRoot} history={history} />
          <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
            {!isActive ? (
              <button className="btn-primary" onClick={doScan} disabled={busy || !root}>
                🔍 Scanner les .db
              </button>
            ) : isCleanupOp ? (
              <button className="btn-danger" onClick={doAbort} style={{ fontSize:14, padding:'10px 20px' }}>
                ⏹ ARRÊTER
              </button>
            ) : (
              <button className="btn-ghost" disabled style={{ opacity:.5 }}>
                Autre opération en cours…
              </button>
            )}
          </div>
        </div>

        {/* v3.10 — option Force */}
        <div style={{ marginTop:14, paddingTop:12, borderTop:'1px dashed var(--border)' }}>
          <label style={{
            display:'flex', alignItems:'flex-start', gap:8,
            textTransform:'none', letterSpacing:0, cursor:'pointer',
            color: force ? 'var(--warning)' : 'var(--muted)', fontSize:12, lineHeight:1.4,
          }}>
            <input type="checkbox" checked={force}
              onChange={e => setForce(e.target.checked)}
              style={{ accentColor:'var(--warning)', marginTop:2 }} />
            <span>
              <strong>⚠ Ignorer la protection audio (mode FORCE)</strong>
              <br />
              <span style={{ color:'var(--muted)', fontSize:11 }}>
                Tous les <code>.db</code> seront marqués supprimables, y compris ceux
                dans des dossiers contenant de la musique. À utiliser si tu veux
                explicitement nettoyer une bibliothèque (ex&nbsp;: vignettes Synology,
                index Plex, etc.). Tu pourras toujours relire la liste avant suppression.
              </span>
            </span>
          </label>
        </div>
      </div>

      {/* Progression */}
      {isActive && isCleanupOp && (
        <div className="card">
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4, gap:10, flexWrap:'wrap' }}>
            <span style={{ fontFamily:'var(--mono)', fontSize:12, color:'var(--muted)',
              overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', flex:'1 1 200px' }}>
              {status.current_file || '…'}
            </span>
            <span style={{ color:'var(--muted)', fontSize:12 }}>
              {fmtNum(status.processed)} / {fmtNum(status.total)} ({status.progress}%)
            </span>
          </div>
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width:`${status.progress}%` }} />
          </div>
        </div>
      )}

      {/* Plan + exécution */}
      {plan && !isActive && (
        <>
          <div className="card">
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
                           marginBottom:12, flexWrap:'wrap', gap:8 }}>
              <h3 style={{ fontSize:14, margin:0 }}>
                Étape 2 — Plan de nettoyage
                {plan.force && (
                  <span style={{
                    marginLeft:8, padding:'2px 8px', fontSize:10, fontWeight:600,
                    background:'var(--warning)', color:'#000', borderRadius:4,
                    letterSpacing:'.05em',
                  }}>⚠ MODE FORCE</span>
                )}
              </h3>
              <span style={{ fontSize:11, color:'var(--muted)' }}>
                Généré le {plan.generated_at?.replace('T', ' ')}
              </span>
            </div>

            <div style={{ fontSize:11, color:'var(--muted)', marginBottom:10 }}>
              Racine : <code>{plan.root}</code>
            </div>

            <div style={{ display:'grid',
                           gridTemplateColumns:'repeat(auto-fit, minmax(140px, 1fr))',
                           gap:10, marginBottom:14 }}>
              <Stat label="Total .db trouvés" value={fmtNum(plan.total)} />
              <Stat label="🟢 Supprimables" value={fmtNum(plan.deletable)}
                    color="var(--success)" />
              <Stat label="🔴 Protégés" value={fmtNum(plan.protected)}
                    color="var(--danger)" />
              <Stat label="Taille à libérer" value={fmtSize(plan.deletable_size)}
                    color={plan.deletable_size > 0 ? 'var(--warning)' : 'var(--muted)'} />
            </div>

            <PlanTable plan={plan} filter={filter} setFilter={setFilter} />
          </div>

          {plan.deletable > 0 && (
            <div className="card">
              <h3 style={{ marginBottom:12, fontSize:14 }}>Étape 3 — Confirmer la suppression</h3>
              <div style={{ display:'flex', gap:12, alignItems:'center', flexWrap:'wrap' }}>
                <label style={{
                  display:'flex', alignItems:'center', gap:6,
                  textTransform:'none', letterSpacing:0, cursor:'pointer',
                  color:'var(--text)', fontSize:13,
                }}>
                  <input type="checkbox" checked={dryRun}
                    onChange={e => setDryRun(e.target.checked)}
                    style={{ accentColor:'var(--accent)' }} />
                  Simulation (aucune écriture)
                </label>
                <button className={dryRun ? 'btn-ghost' : 'btn-danger'} onClick={doExecute} disabled={busy}>
                  {dryRun
                    ? `🧪 Simuler la suppression (${plan.deletable} fichiers)`
                    : `🗑 Supprimer ${plan.deletable} fichiers définitivement`}
                </button>
              </div>
              {!dryRun && (
                <div style={{ marginTop:8, fontSize:11, color:'var(--danger)' }}>
                  ⚠ Action IRRÉVERSIBLE — les fichiers seront définitivement supprimés
                </div>
              )}
            </div>
          )}
        </>
      )}

      {!plan && !isActive && (
        <div className="card">
          <div style={{ color:'var(--muted)', fontSize:13, textAlign:'center', padding:20 }}>
            Aucun plan disponible. Lance un scan pour identifier les fichiers <code>.db</code>.
          </div>
        </div>
      )}
    </div>
  )
}


function Stat({ label, value, color='var(--text)' }) {
  return (
    <div style={{ padding:12, background:'var(--bg)', borderRadius:6, textAlign:'center' }}>
      <div style={{ fontSize:10, color:'var(--muted)', textTransform:'uppercase',
                     letterSpacing:'.05em', marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:20, fontWeight:700, color }}>{value}</div>
    </div>
  )
}

const th = { textAlign:'left', padding:'8px 10px', fontWeight:600, fontSize:11, color:'var(--muted)',
             textTransform:'uppercase', letterSpacing:'.05em', borderBottom:'1px solid var(--border)' }
const td = { padding:'6px 10px', color:'var(--text)' }
