import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchAPI } from '../api/client'
import { useWebSocketContext } from '../api/useWebSocketContext'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { stateLabel } from '../i18n/labels'
import type { Project, Video, Scene, Request, Character, WSEvent } from '../types'
import { sceneStageStatus, videoStageBreakdown } from '../lib/stageStats'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from '../components/ui/card'
import { Progress } from '../components/ui/progress'
import { Badge } from '../components/ui/badge'

type VideoWithProject = Video & { projectName: string; projectId: string }
type T = (key: TranslationKey, params?: Record<string, string | number>) => string

function describeEvent(t: T, e: WSEvent, requests: Request[]): string {
  const data = (e.data ?? {}) as { id?: string; status?: string; type?: string; error?: string; count?: number }
  if (e.type === 'request_update') {
    const req = requests.find(r => r.id === data.id)
    const label = req?.type ?? data.type ?? t('dashboard.event.request')
    if (data.status === 'PROCESSING') return t('dashboard.event.started', { label })
    if (data.status === 'COMPLETED') return t('dashboard.event.completed', { label })
    if (data.status === 'FAILED') return t('dashboard.event.failed', { label }) + (data.error ? ' · ' + String(data.error).slice(0, 80) : '')
    return t('dashboard.event.generic', { label, status: data.status ?? '' })
  }
  if (e.type === 'urls_refreshed') return t('dashboard.event.refreshedUrls', { n: data.count ?? 0 })
  if (e.type === 'worker_tick') return t('dashboard.event.workerTick')
  return e.type
}

export default function DashboardPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { events, lastEvent } = useWebSocketContext()
  const [projects, setProjects] = useState<Project[]>([])
  const [videosByProject, setVideosByProject] = useState<Record<string, Video[]>>({})
  const [scenesByVideo, setScenesByVideo] = useState<Record<string, Scene[]>>({})
  const [requests, setRequests] = useState<Request[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    const active = await fetchAPI<Project[]>('/api/projects?status=ACTIVE')
    const videoLists = await Promise.all(active.map(p => fetchAPI<Video[]>(`/api/videos?project_id=${p.id}`)))
    const vbp: Record<string, Video[]> = {}
    active.forEach((p, i) => { vbp[p.id] = videoLists[i] })

    const allVideos = videoLists.flat()
    const sceneLists = await Promise.all(allVideos.map(v => fetchAPI<Scene[]>(`/api/scenes?video_id=${v.id}`)))
    const sbv: Record<string, Scene[]> = {}
    allVideos.forEach((v, i) => { sbv[v.id] = sceneLists[i] })

    const [allRequests, allChars] = await Promise.all([
      fetchAPI<Request[]>('/api/requests'),
      fetchAPI<Character[]>('/api/characters'),
    ])

    setProjects(active)
    setVideosByProject(vbp)
    setScenesByVideo(sbv)
    setRequests(allRequests)
    setCharacters(allChars)
    setLoading(false)
  }, [])

  useEffect(() => { Promise.resolve().then(load) }, [load])

  useEffect(() => {
    if (!lastEvent) return
    if (lastEvent.type === 'request_update' || lastEvent.type === 'urls_refreshed') Promise.resolve().then(load)
  }, [lastEvent, load])

  if (loading) {
    return <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('dashboard.loading')}</div>
  }

  const allVideos: VideoWithProject[] = projects.flatMap(p =>
    (videosByProject[p.id] ?? []).map(v => ({ ...v, projectName: p.name, projectId: p.id }))
  )

  const scenesInFlight = allVideos.reduce((sum, v) => {
    const scenes = scenesByVideo[v.id] ?? []
    return sum + scenes.filter(s =>
      (['image', 'video', 'upscale'] as const).some(stage => sceneStageStatus(s, stage) === 'PROCESSING')
    ).length
  }, 0)

  const todayStr = new Date().toDateString()
  const completedToday = requests.filter(r => r.status === 'COMPLETED' && new Date(r.updated_at).toDateString() === todayStr).length
  const failed24h = requests.filter(r => r.status === 'FAILED' && new Date().getTime() - new Date(r.updated_at).getTime() <= 24 * 3600 * 1000).length

  const kpis: { id: string; labelKey: TranslationKey; value: number; color: string; note: string }[] = [
    { id: 'scenesInFlight', labelKey: 'dashboard.kpi.scenesInFlight', value: scenesInFlight, color: 'var(--yellow)', note: t('dashboard.kpi.note.scenesInFlight', { n: allVideos.length }) },
    { id: 'completedToday', labelKey: 'dashboard.kpi.completedToday', value: completedToday, color: 'var(--green)', note: t('dashboard.kpi.note.completedToday', { n: requests.length }) },
    { id: 'failed24h', labelKey: 'dashboard.kpi.failed24h', value: failed24h, color: 'var(--red)', note: t('dashboard.kpi.note.failed24h', { n: requests.filter(r => r.status === 'FAILED').length }) },
    { id: 'activeProjects', labelKey: 'dashboard.kpi.activeProjects', value: projects.length, color: 'var(--text)', note: t('dashboard.kpi.note.activeProjects', { n: allVideos.length }) },
  ]

  const throughputRows = allVideos.map(v => {
    const scenes = scenesByVideo[v.id] ?? []
    const breakdown = videoStageBreakdown(scenes)
    const total = scenes.length
    const vidRequests = requests.filter(r => r.video_id === v.id)
    const anyProcessing = vidRequests.some(r => r.status === 'PROCESSING')
    const allDone = total > 0 && (['image', 'video', 'upscale'] as const).every(k => breakdown[k].done === total)
    const state: 'COMPLETED' | 'RUNNING' | 'QUEUED' = allDone ? 'COMPLETED' : anyProcessing ? 'RUNNING' : 'QUEUED'
    return { video: v, breakdown, total, state }
  })

  const sceneIndex = new Map<string, { project: Project; video: Video; scene: Scene }>()
  projects.forEach(p => (videosByProject[p.id] ?? []).forEach(v => (scenesByVideo[v.id] ?? []).forEach(s => sceneIndex.set(s.id, { project: p, video: v, scene: s }))))
  const charIndex = new Map(characters.map(c => [c.id, c]))

  const attentionRows = requests
    .filter(r => r.status === 'FAILED')
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
    .slice(0, 6)
    .map(r => {
      const s = r.scene_id ? sceneIndex.get(r.scene_id) : undefined
      const c = r.character_id ? charIndex.get(r.character_id) : undefined
      const label = s ? `${s.video.title} · scene ${s.scene.display_order + 1}` : c ? c.name : r.id.slice(0, 8)
      return { id: r.id, label, type: r.type, time: r.updated_at, error: r.error_message, projectId: s?.project.id }
    })

  const recentEvents = events.slice(0, 8)

  return (
    <div className="flex flex-col gap-5">
      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-3.5">
        {kpis.map(k => (
          <Card key={k.id} className="py-4 gap-2">
            <CardHeader>
              <CardDescription className="text-[10px] tracking-widest">{t(k.labelKey)}</CardDescription>
              <CardTitle>
                <span className="text-2xl tracking-tight" style={{ color: k.color }}>{k.value}</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{k.note}</span>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 items-start" style={{ gridTemplateColumns: '1.55fr 1fr' }}>
        {/* Throughput table */}
        <Card className="py-4">
          <CardHeader>
            <CardTitle className="text-xs tracking-widest uppercase">{t('dashboard.throughput.title')}</CardTitle>
            <CardDescription className="text-[11px]">{t('dashboard.throughput.desc')}</CardDescription>
          </CardHeader>
          <CardContent>
            {throughputRows.length === 0 ? (
              <div className="text-xs py-6 text-center" style={{ color: 'var(--muted)' }}>{t('dashboard.throughput.empty')}</div>
            ) : (
              <div className="flex flex-col gap-3">
                {throughputRows.map(r => (
                  <div key={r.video.id} className="flex items-center gap-3.5 cursor-pointer" onClick={() => navigate(`/projects/${r.video.projectId}?tab=videos`)}>
                    <div className="flex flex-col min-w-0" style={{ width: 160 }}>
                      <span className="text-[11px] truncate">{r.video.title}</span>
                      <span className="text-[9px] tracking-wide truncate" style={{ color: 'var(--muted)' }}>{r.video.projectName}</span>
                    </div>
                    {(['image', 'video', 'upscale'] as const).map(stage => {
                      const c = r.breakdown[stage]
                      const pct = r.total > 0 ? Math.round((c.done / r.total) * 100) : 0
                      return (
                        <div key={stage} className="flex flex-col gap-1" style={{ width: 76 }}>
                          <span className="text-[10px]" style={{ color: pct === 100 ? 'var(--green)' : pct === 0 ? 'var(--muted)' : 'var(--yellow)' }}>{pct}%</span>
                          <Progress value={pct} className="h-1" />
                        </div>
                      )
                    })}
                    <Badge variant={r.state === 'COMPLETED' ? 'secondary' : r.state === 'RUNNING' ? 'default' : 'outline'} className="ml-auto">
                      {stateLabel(t, r.state)}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Needs attention */}
        <Card className="py-4">
          <CardHeader>
            <CardTitle className="text-xs tracking-widest uppercase">{t('dashboard.attention.title')}</CardTitle>
            <CardDescription className="text-[11px]">{t('dashboard.attention.desc')}</CardDescription>
            <CardAction>
              <span className="text-[11px]" style={{ color: 'var(--red)' }}>{attentionRows.length}</span>
            </CardAction>
          </CardHeader>
          <CardContent>
            {attentionRows.length === 0 ? (
              <div className="text-xs py-6 text-center" style={{ color: 'var(--muted)' }}>{t('dashboard.attention.empty')}</div>
            ) : (
              <div className="flex flex-col gap-2">
                {attentionRows.map(a => (
                  <div
                    key={a.id}
                    className="rounded-md p-2.5 flex flex-col gap-1 cursor-pointer"
                    style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderLeft: '2px solid var(--red)' }}
                    onClick={() => a.projectId && navigate(`/projects/${a.projectId}?tab=pipeline`)}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] tracking-wide">{a.label}</span>
                      <span className="text-[9px] tracking-widest" style={{ color: 'var(--red)' }}>{a.type}</span>
                      <span className="ml-auto text-[9px]" style={{ color: 'var(--muted)' }}>{new Date(a.time).toLocaleTimeString()}</span>
                    </div>
                    {a.error && <span className="text-[10px] leading-snug" style={{ color: 'var(--muted)' }}>{a.error.slice(0, 140)}</span>}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Event stream */}
      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('dashboard.events.title')}</CardTitle>
          <CardAction>
            <span className="text-[10px] tracking-wide cursor-pointer" style={{ color: 'var(--accent)' }} onClick={() => navigate('/logs')}>{t('dashboard.events.openLogs')}</span>
          </CardAction>
        </CardHeader>
        <CardContent>
          {recentEvents.length === 0 ? (
            <div className="text-xs py-6 text-center" style={{ color: 'var(--muted)' }}>{t('dashboard.events.empty')}</div>
          ) : (
            <div className="flex flex-col gap-1">
              {recentEvents.map((e, i) => (
                <div key={i} className="flex gap-3.5 text-[11px] py-0.5">
                  <span style={{ color: 'var(--muted)', width: 64, flexShrink: 0 }}>{new Date(e.timestamp).toLocaleTimeString()}</span>
                  <span style={{ color: 'var(--accent)', width: 130, flexShrink: 0 }}>{e.type}</span>
                  <span style={{ color: 'var(--muted)' }}>{describeEvent(t, e, requests)}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
