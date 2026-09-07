import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchAPI, patchAPI } from '../api/client'
import type { Project, Character, Video, Scene, Request } from '../types'
import EditableText from '../components/projects/EditableText'
import PipelineView from '../components/pipeline/PipelineView'
import { count, charStatus, videoStageBreakdown, type SceneStage } from '../lib/stageStats'
import { useTranslation } from '../i18n/useTranslation'
import { statusLabel, stateLabel, projectStatusLabel, stageLowerLabel } from '../i18n/labels'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Progress } from '../components/ui/progress'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs'
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from '../components/ui/table'
import { Button } from '../components/ui/button'

type Tab = 'overview' | 'characters' | 'videos' | 'pipeline'
const STAGE_KEYS: ('refs' | SceneStage)[] = ['refs', 'image', 'video', 'upscale']

interface Props {
  projectId: string
  onBack: () => void
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

const STATUS_COLOR: Record<string, string> = {
  COMPLETED: 'var(--green)',
  PROCESSING: 'var(--yellow)',
  FAILED: 'var(--red)',
  PENDING: 'var(--muted)',
}

export default function ProjectDetailPage({ projectId, onBack }: Props) {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = (searchParams.get('tab') as Tab) ?? 'overview'

  const [project, setProject] = useState<Project | null>(null)
  const [characters, setCharacters] = useState<Character[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [scenesByVideo, setScenesByVideo] = useState<Record<string, Scene[]>>({})
  const [requests, setRequests] = useState<Request[]>([])
  const [loading, setLoading] = useState(true)
  const [pipelineVideoId, setPipelineVideoId] = useState<string>('')

  const fetchAll = useCallback(async () => {
    setLoading(true)
    const [proj, chars, vids] = await Promise.all([
      fetchAPI<Project>(`/api/projects/${projectId}`),
      fetchAPI<Character[]>(`/api/projects/${projectId}/characters`),
      fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`),
    ])
    const sceneLists = await Promise.all(vids.map(v => fetchAPI<Scene[]>(`/api/scenes?video_id=${v.id}`)))
    const sbv: Record<string, Scene[]> = {}
    vids.forEach((v, i) => { sbv[v.id] = sceneLists[i] })
    const reqs = await fetchAPI<Request[]>(`/api/requests?project_id=${projectId}`)

    setProject(proj)
    setCharacters(chars)
    setVideos(vids)
    setScenesByVideo(sbv)
    setRequests(reqs)
    setPipelineVideoId(prev => prev && vids.some(v => v.id === prev) ? prev : (vids[0]?.id ?? ''))
    setLoading(false)
  }, [projectId])

  useEffect(() => { Promise.resolve().then(fetchAll) }, [fetchAll])

  function setTab(t: Tab) {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('tab', t)
      return next
    })
  }

  async function patchProject(field: string, value: string) {
    await patchAPI(`/api/projects/${projectId}`, { [field]: value })
    fetchAll()
  }

  async function patchChar(cid: string, field: string, value: string) {
    await patchAPI(`/api/characters/${cid}`, { [field]: value })
    fetchAll()
  }

  if (loading || !project) {
    return <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.loading')}</div>
  }

  const allScenes = videos.flatMap(v => scenesByVideo[v.id] ?? [])
  const stageRollup: Record<'refs' | SceneStage, ReturnType<typeof count>> = {
    refs: count(characters.map(c => charStatus(c, requests))),
    ...videoStageBreakdown(allScenes),
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline gap-3">
            <h1 className="m-0 text-lg font-semibold" style={{ color: 'var(--text)' }}>{project.name}</h1>
            <Badge variant="outline">{project.material}</Badge>
            <Badge variant="outline">{projectStatusLabel(t, project.status)}</Badge>
          </div>
          <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
            {t('projectDetail.header', { id: project.id, date: formatDate(project.created_at), videos: videos.length, scenes: allScenes.length })}
          </span>
        </div>
        <Button variant="ghost" size="sm" onClick={onBack}>{t('projectDetail.back')}</Button>
      </div>

      <Tabs value={tab} onValueChange={v => setTab(v as Tab)}>
        <TabsList>
          <TabsTrigger value="overview">{t('projectDetail.tab.overview')}</TabsTrigger>
          <TabsTrigger value="characters">{t('projectDetail.tab.characters', { n: characters.length })}</TabsTrigger>
          <TabsTrigger value="videos">{t('projectDetail.tab.videos', { n: videos.length })}</TabsTrigger>
          <TabsTrigger value="pipeline">{t('projectDetail.tab.pipeline')}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="pt-4">
          <div className="grid gap-4" style={{ gridTemplateColumns: '1.4fr 1fr' }}>
            <Card className="py-4">
              <CardHeader>
                <CardTitle className="text-xs tracking-widest uppercase">{t('projectDetail.card.projectFields')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-col gap-3.5">
                  {[
                    { label: t('projectDetail.field.name'), value: project.name, field: 'name', multiline: false },
                    { label: t('projectDetail.field.description'), value: project.description ?? '', field: 'description', multiline: true },
                    { label: t('projectDetail.field.story'), value: project.story ?? '', field: 'story', multiline: true },
                  ].map(f => (
                    <div key={f.field} className="flex flex-col gap-1">
                      <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{f.label}</span>
                      <div className="rounded-md px-2.5 py-2 text-xs" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
                        <EditableText value={f.value} onSave={v => patchProject(f.field, v)} multiline={f.multiline} className="text-xs" />
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <div className="flex flex-col gap-4">
              <Card className="py-4">
                <CardHeader>
                  <CardTitle className="text-xs tracking-widest uppercase">{t('projectDetail.card.narrator')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-1.5 text-[11px]">
                    <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>{t('projectDetail.narrator.enabled')}</span><span>{project.narrator_voice ? t('projectDetail.true') : t('projectDetail.false')}</span></div>
                    <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>{t('projectDetail.narrator.voice')}</span><span>{project.narrator_voice ?? t('projectDetail.noNarration')}</span></div>
                    <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>{t('projectDetail.narrator.refAudio')}</span><span>{project.narrator_ref_audio ?? t('common.dash')}</span></div>
                  </div>
                </CardContent>
              </Card>

              <Card className="py-4">
                <CardHeader>
                  <CardTitle className="text-xs tracking-widest uppercase">{t('projectDetail.card.stageRollup')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-2.5">
                    {STAGE_KEYS.map(key => {
                      const c = stageRollup[key]
                      const pct = c.total > 0 ? Math.round((c.done / c.total) * 100) : 0
                      return (
                        <div key={key} className="grid items-center gap-2.5" style={{ gridTemplateColumns: '60px 1fr 50px' }}>
                          <span className="text-[10px] tracking-wide uppercase" style={{ color: 'var(--text)' }}>{stageLowerLabel(t, key)}</span>
                          <Progress value={pct} className="h-1" />
                          <span className="text-[10px] text-right" style={{ color: 'var(--muted)' }}>{c.done}/{c.total}</span>
                        </div>
                      )
                    })}
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="characters" className="pt-4">
          {characters.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.noCharacters')}</div>
          ) : (
            <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              {characters.map(ch => {
                const st = charStatus(ch, requests)
                return (
                  <Card key={ch.id} className="py-0 gap-0 overflow-hidden h-full">
                    <div className="relative flex items-center justify-center" style={{ aspectRatio: '1/1', background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                      {ch.reference_image_url ? (
                        <img src={ch.reference_image_url} alt={ch.name} className="w-full h-full object-cover" />
                      ) : (
                        <span className="text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>{st === 'PROCESSING' ? t('projectDetail.character.generating') : t('projectDetail.character.noReference')}</span>
                      )}
                      <span className="absolute top-2 left-2 flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[9px] tracking-wide" style={{ background: 'rgba(10,10,20,0.8)', color: STATUS_COLOR[st] }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: STATUS_COLOR[st] }} />{statusLabel(t, st)}
                      </span>
                    </div>
                    <div className="p-3 flex flex-col gap-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold">{ch.name}</span>
                        <Badge variant="outline">{ch.entity_type}</Badge>
                      </div>
                      <div className="text-[11px]" style={{ color: 'var(--muted)' }}>
                        <EditableText value={ch.description ?? ''} onSave={v => patchChar(ch.id, 'description', v)} multiline className="text-[11px]" />
                      </div>
                      <span className="text-[9px] tracking-wide" style={{ color: 'var(--muted)' }}>{t('projectDetail.character.updated', { date: formatDate(ch.updated_at) })}</span>
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="videos" className="pt-4">
          {videos.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.noVideos')}</div>
          ) : (
            <Card className="py-4">
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('projectDetail.table.video')}</TableHead>
                      <TableHead>{t('projectDetail.table.scenes')}</TableHead>
                      <TableHead>{t('projectDetail.table.progress')}</TableHead>
                      <TableHead>{t('projectDetail.table.state')}</TableHead>
                      <TableHead></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {videos.map(v => {
                      const scenes = scenesByVideo[v.id] ?? []
                      const breakdown = videoStageBreakdown(scenes)
                      const stages: SceneStage[] = ['image', 'video', 'upscale']
                      const totalSlots = scenes.length * stages.length
                      const doneSlots = stages.reduce((sum, s) => sum + breakdown[s].done, 0)
                      const pct = totalSlots > 0 ? Math.round((doneSlots / totalSlots) * 100) : 0
                      const anyProcessing = requests.some(r => r.video_id === v.id && r.status === 'PROCESSING')
                      const state: 'COMPLETED' | 'RUNNING' | 'QUEUED' = pct === 100 && scenes.length > 0 ? 'COMPLETED' : anyProcessing ? 'RUNNING' : 'QUEUED'
                      return (
                        <TableRow key={v.id}>
                          <TableCell>
                            <div className="text-xs">{v.title}</div>
                            <div className="text-[9px]" style={{ color: 'var(--muted)' }}>{v.id.slice(0, 8)}</div>
                          </TableCell>
                          <TableCell className="text-xs" style={{ color: 'var(--muted)' }}>{scenes.length}</TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1" style={{ width: 120 }}>
                              <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{pct}%</span>
                              <Progress value={pct} className="h-1" />
                            </div>
                          </TableCell>
                          <TableCell><Badge variant={state === 'COMPLETED' ? 'secondary' : state === 'RUNNING' ? 'default' : 'outline'}>{stateLabel(t, state)}</Badge></TableCell>
                          <TableCell>
                            <span
                              className="text-[10px] cursor-pointer"
                              style={{ color: 'var(--accent)' }}
                              onClick={() => { setPipelineVideoId(v.id); setTab('pipeline') }}
                            >
                              {t('projectDetail.pipelineLink')}
                            </span>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="pipeline" className="pt-4">
          {videos.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.pipeline.noVideos')}</div>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-2">
                <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('projectDetail.pipeline.videoLabel')}</span>
                {videos.map(v => (
                  <Button key={v.id} variant={v.id === pipelineVideoId ? 'default' : 'outline'} size="sm" onClick={() => setPipelineVideoId(v.id)}>
                    {v.title}
                  </Button>
                ))}
              </div>
              {pipelineVideoId && <PipelineView projectId={projectId} videoId={pipelineVideoId} />}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
