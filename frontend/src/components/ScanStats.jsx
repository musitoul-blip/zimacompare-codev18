import { useState, useEffect } from 'react'
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from 'recharts'
import { api, fmtSize, fmtNum } from '../api.js'

const STATUS_COLORS = {
  new:       '#22c55e',
  different: '#f59e0b',
  deleted:   '#ef4444',
  identical: '#9ca3af',
  error:     '#dc2626',
}
const STATUS_LABELS = {
  new: 'Nouveaux', different: 'Modifiés', deleted: 'Supprimés',
  identical: 'Identiques', error: 'Erreurs',
}
const BUCKET_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#a78bfa', '#fb923c']

// FIX : style commun pour tous les tooltips recharts — fond plus clair et texte blanc.
const TOOLTIP_CONTENT_STYLE = {
  background: '#1e293b',          // au lieu de #0f172a (trop sombre)
  border: '1px solid #475569',
  borderRadius: 6,
  fontSize: 12,
  color: '#f1f5f9',                // texte clair
  padding: '8px 12px',
  boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
}
const TOOLTIP_LABEL_STYLE = { color: '#cbd5e1', fontWeight: 600, marginBottom: 4 }
const TOOLTIP_ITEM_STYLE  = { color: '#f1f5f9' }   // FORCE le texte des items en blanc


function StatCard({ label, value, sublabel, color = 'var(--text)' }) {
  return (
    <div style={{ padding: 14, background: 'var(--bg)', borderRadius: 8, textAlign: 'center' }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase',
                    letterSpacing: '.05em', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
      {sublabel && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{sublabel}</div>}
    </div>
  )
}


function DonutChart({ data, title, total }) {
  if (!data.length) return null
  return (
    <div style={{ flex: '1 1 280px', minWidth: 0 }}>
      <div style={{ fontSize: 12, color: 'var(--muted)', textTransform: 'uppercase',
                    letterSpacing: '.05em', marginBottom: 6, textAlign: 'center' }}>{title}</div>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie data={data} cx="50%" cy="50%" innerRadius={50} outerRadius={80}
               paddingAngle={2} dataKey="value">
            {data.map((entry, i) => (
              <Cell key={i} stroke="none" fill={entry.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={TOOLTIP_CONTENT_STYLE}
            labelStyle={TOOLTIP_LABEL_STYLE}
            itemStyle={TOOLTIP_ITEM_STYLE}
            // On force la couleur blanche dans le texte de retour (sinon recharts
            // applique la couleur de la série au texte → invisible sur fond sombre)
            formatter={(value, name) => {
              const pct = total > 0 ? (value / total * 100).toFixed(1) : 0
              return [
                <span style={{ color: '#f1f5f9' }}>{fmtNum(value)} ({pct}%)</span>,
                <span style={{ color: '#cbd5e1' }}>{name}</span>,
              ]
            }} />
          <Legend wrapperStyle={{ fontSize: 11 }} iconSize={10} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}


export default function ScanStats({ status }) {
  const [stats, setStats]   = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  useEffect(() => {
    setLoading(true)
    api.scanStats().then(setStats).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 20, color: 'var(--muted)' }}>Chargement…</div>
  if (error)   return <div style={{ padding: 12, color: 'var(--danger)', background: '#450a0a', borderRadius: 6 }}>
    Impossible de charger les statistiques : {error}
  </div>
  if (!stats || !stats.total) return null

  const _bs = stats.by_status || {}
  const totalDiff = (_bs.new || 0) + (_bs.different || 0) + (_bs.deleted || 0)

  const statusData = Object.entries(stats.by_status || {})
    .filter(([_, v]) => v > 0)
    .map(([k, v]) => ({ name: STATUS_LABELS[k] || k, value: v, color: STATUS_COLORS[k] || '#9ca3af' }))

  const bucketData = Object.entries(stats.size_buckets || {})
    .filter(([_, v]) => v > 0)
    .map(([k, v], i) => ({ name: k, value: v, color: BUCKET_COLORS[i % BUCKET_COLORS.length] }))

  const extData = Object.entries(stats.by_extension || {}).map(([ext, c]) => ({ name: '.' + ext, count: c }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      <div>
        <h4 style={hLbl}>Vue d'ensemble</h4>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
          <StatCard label="Total entrées"   value={fmtNum(stats.total)} />
          <StatCard label="Fichiers"        value={fmtNum(stats.files)} />
          <StatCard label="Dossiers"        value={fmtNum(stats.dirs)} />
          <StatCard label="Écarts détectés" value={fmtNum(totalDiff)}
                    color={totalDiff > 0 ? 'var(--warning)' : 'var(--success)'} />
          {/* v3.12 — entrées filtrées par .zimaignore */}
          {status?.ignored_count > 0 && (
            <StatCard label="Ignorés .zimaignore" value={fmtNum(status.ignored_count)}
                      sublabel="filtrés au scan" color="var(--muted)" />
          )}
        </div>
      </div>

      <div>
        <h4 style={hLbl}>Volumétrie</h4>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
          <StatCard label="Taille source" value={fmtSize(stats.total_source_size)} />
          <StatCard label="Taille cible"  value={fmtSize(stats.total_target_size)} />
          <StatCard label="À synchroniser"
                    value={fmtSize(stats.bytes_new + stats.bytes_different)}
                    sublabel={stats.bytes_deleted > 0 ? `+ ${fmtSize(stats.bytes_deleted)} à supprimer` : null}
                    color={(stats.bytes_new + stats.bytes_different) > 0 ? 'var(--warning)' : 'var(--success)'} />
        </div>
      </div>

      <div>
        <h4 style={hLbl}>Répartition</h4>
        <div className="card" style={{ padding: 14, display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          <DonutChart data={statusData} title="Par statut"            total={stats.total} />
          <DonutChart data={bucketData} title="Par taille de fichier" total={stats.files} />
        </div>
      </div>

      {extData.length > 0 && (
        <div>
          <h4 style={hLbl}>Top 15 extensions</h4>
          <div className="card" style={{ padding: 14 }}>
            <ResponsiveContainer width="100%" height={Math.max(200, extData.length * 22)}>
              <BarChart data={extData} layout="vertical" margin={{ top: 0, right: 30, left: 10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis type="category" dataKey="name" width={60}
                       tick={{ fontSize: 11, fill: '#cbd5e1', fontFamily: 'monospace' }} />
                <Tooltip
                  contentStyle={TOOLTIP_CONTENT_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  itemStyle={TOOLTIP_ITEM_STYLE}
                  formatter={(v) => [<span style={{ color: '#f1f5f9' }}>{fmtNum(v)}</span>, 'Fichiers']} />
                <Bar dataKey="count" fill="#3b82f6" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {(stats.biggest_diffs || []).length > 0 && (
        <div>
          <h4 style={hLbl}>Top 10 plus gros écarts à synchroniser</h4>
          <TopFilesTable rows={stats.biggest_diffs || []} />
        </div>
      )}

      <details>
        <summary style={{ cursor: 'pointer', ...hLbl, marginBottom: 10 }}>
          ▶ Top 10 plus gros fichiers (tous statuts)
        </summary>
        <TopFilesTable rows={stats.biggest_files} />
      </details>
    </div>
  )
}


function TopFilesTable({ rows }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden', overflowX: 'auto' }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', minWidth: 400 }}>
        <tbody>
          {rows.map((f, i) => (
            <tr key={i} style={{ borderTop: i > 0 ? '1px solid var(--border)' : 'none' }}>
              <td style={{ padding: '8px 12px', width: 90 }}>
                <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: '.05em',
                               color: STATUS_COLORS[f.status] || '#9ca3af' }}>
                  {STATUS_LABELS[f.status] || f.status}
                </span>
              </td>
              <td style={{ padding: '8px 12px', fontFamily: 'var(--mono)',
                           wordBreak: 'break-all', color: 'var(--text)' }}>{f.path}</td>
              <td style={{ padding: '8px 12px', textAlign: 'right',
                           fontFamily: 'var(--mono)', color: 'var(--muted)', width: 90 }}>
                {fmtSize(f.size)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const hLbl = {
  fontSize: 12, color: 'var(--muted)', textTransform: 'uppercase',
  letterSpacing: '.08em', marginBottom: 10,
}
