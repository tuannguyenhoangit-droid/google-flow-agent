import { useState, useEffect, useCallback } from 'react'
import { fetchAPI } from '../../api/client'
import { useWebSocketContext } from '../../api/useWebSocketContext'
import { useTranslation } from '../../i18n/useTranslation'
import { statusLabel } from '../../i18n/labels'
import type { Request, Character, StatusType } from '../../types'
import { Card, CardContent } from '../ui/card'
import { Button } from '../ui/button'
import { ScrollArea } from '../ui/scroll-area'

interface LogRow {
  id: string
  time: string
  type: string
  status: StatusType
  severity: 'info' | 'error'
  target: string
}

export default function LogViewer() {
  const { t } = useTranslation()
  const { lastEvent } = useWebSocketContext()
  const [requests, setRequests] = useState<Request[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [sevFilter, setSevFilter] = useState<'all' | 'info' | 'error'>('all')
  const [paused, setPaused] = useState(false)

  const load = useCallback(async () => {
    const [reqs, chars] = await Promise.all([
      fetchAPI<Request[]>('/api/requests'),
      fetchAPI<Character[]>('/api/characters'),
    ])
    setRequests(reqs)
    setCharacters(chars)
  }, [])

  useEffect(() => { Promise.resolve().then(load) }, [load])

  useEffect(() => {
    if (paused || !lastEvent) return
    if (lastEvent.type === 'request_update' || lastEvent.type === 'urls_refreshed') Promise.resolve().then(load)
  }, [lastEvent, paused, load])

  const charIndex = new Map(characters.map(c => [c.id, c.name]))

  const rows: LogRow[] = requests
    .slice()
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
    .map(r => ({
      id: r.id,
      time: r.updated_at,
      type: r.type,
      status: r.status,
      severity: r.status === 'FAILED' ? 'error' : 'info',
      target: r.character_id ? (charIndex.get(r.character_id) ?? r.character_id.slice(0, 8))
        : r.scene_id ? t('logs.targetScene', { id: r.scene_id.slice(0, 8) })
        : r.id.slice(0, 8),
    }))

  const types = Array.from(new Set(requests.map(r => r.type))).sort()

  const q = query.trim().toLowerCase()
  const filtered = rows.filter(row => {
    if (typeFilter !== 'all' && row.type !== typeFilter) return false
    if (sevFilter !== 'all' && row.severity !== sevFilter) return false
    if (q && !(row.type + ' ' + row.target + ' ' + row.status).toLowerCase().includes(q)) return false
    return true
  })

  return (
    <div className="flex flex-col gap-3.5" style={{ height: 'calc(100vh - 130px)' }}>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t('logs.searchPlaceholder')}
          className="text-xs px-2.5 py-1.5 rounded outline-none"
          style={{ width: 280, background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
        />
        <select
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
          className="text-xs px-2 py-1.5 rounded outline-none"
          style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
        >
          <option value="all">{t('logs.allTypes')}</option>
          {types.map(rt => <option key={rt} value={rt}>{rt}</option>)}
        </select>
        <div className="flex gap-1">
          {(['all', 'info', 'error'] as const).map(s => (
            <Button key={s} variant={sevFilter === s ? 'default' : 'outline'} size="sm" onClick={() => setSevFilter(s)}>
              {s === 'all' ? t('logs.sev.any') : s === 'info' ? t('logs.sev.info') : t('logs.sev.error')}
            </Button>
          ))}
        </div>
        <Button variant="outline" size="sm" className="ml-auto" onClick={() => setPaused(p => !p)}>
          {paused ? t('logs.resume') : t('logs.pause')}
        </Button>
      </div>

      <Card className="py-0 flex-1 min-h-0 overflow-hidden">
        <div
          className="grid gap-3.5 px-4 py-2.5 text-[9px] tracking-widest"
          style={{ gridTemplateColumns: '150px 200px 90px 140px 1fr', borderBottom: '1px solid var(--border)', color: 'var(--muted)' }}
        >
          <span>{t('logs.table.time')}</span><span>{t('logs.table.type')}</span><span>{t('logs.table.status')}</span><span>{t('logs.table.target')}</span><span>{t('logs.table.detail')}</span>
        </div>
        <ScrollArea className="h-full">
          <CardContent className="py-0">
            {filtered.length === 0 ? (
              <div className="py-10 text-center text-xs" style={{ color: 'var(--muted)' }}>{t('logs.empty')}</div>
            ) : (
              filtered.map(row => {
                const req = requests.find(r => r.id === row.id)
                return (
                  <div
                    key={row.id}
                    className="grid gap-3.5 py-2 text-[11px]"
                    style={{ gridTemplateColumns: '150px 200px 90px 140px 1fr', borderBottom: '1px solid var(--border)' }}
                  >
                    <span style={{ color: 'var(--muted)' }}>{new Date(row.time).toLocaleString()}</span>
                    <span style={{ color: 'var(--accent)' }}>{row.type}</span>
                    <span style={{ color: row.severity === 'error' ? 'var(--red)' : row.status === 'PROCESSING' ? 'var(--yellow)' : 'var(--green)' }}>{statusLabel(t, row.status)}</span>
                    <span style={{ color: 'var(--muted)' }}>{row.target}</span>
                    <span style={{ color: 'var(--muted)' }}>{req?.error_message ?? (req?.retry_count ? t('logs.detailRetry', { n: req.retry_count }) : '')}</span>
                  </div>
                )
              })
            )}
          </CardContent>
        </ScrollArea>
      </Card>

      <div className="flex items-center gap-3.5 text-[10px]" style={{ color: 'var(--muted)' }}>
        <span>{t('logs.footerCount', { n: filtered.length, m: rows.length })}</span>
        <span>· {paused ? t('logs.tailPaused') : t('logs.live')}</span>
      </div>
    </div>
  )
}
