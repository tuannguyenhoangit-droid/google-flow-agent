import { useState } from 'react'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription, SheetFooter } from '../ui/sheet'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../ui/tabs'
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '../ui/accordion'
import { Table, TableBody, TableRow, TableCell } from '../ui/table'
import { Button } from '../ui/button'
import { useTranslation } from '../../i18n/useTranslation'
import { statusLabel, dimensionLabel, stageLowerLabel } from '../../i18n/labels'
import type { Scene, Character, Request, SceneReview, StatusType } from '../../types'

type SceneStage = 'image' | 'video' | 'upscale'

const STATUS_COLORS: Record<StatusType, string> = {
  COMPLETED: 'var(--green)',
  PROCESSING: 'var(--yellow)',
  PENDING: 'var(--muted)',
  FAILED: 'var(--red)',
}

const VERDICT_COLORS: Record<string, string> = {
  excellent: 'var(--green)', good: 'var(--green)', acceptable: 'var(--yellow)', poor: 'var(--red)', unusable: 'var(--red)',
}

const SEV_COLORS: Record<string, string> = {
  CRITICAL: 'var(--red)', HIGH: 'var(--yellow)', MINOR: 'var(--muted)',
}

function scoreColor(v: number) { return v >= 8 ? 'var(--green)' : v >= 6 ? 'var(--yellow)' : 'var(--red)' }

const STAGE_TYPES: Record<SceneStage, string[]> = {
  image: ['GENERATE_IMAGE', 'REGENERATE_IMAGE', 'EDIT_IMAGE'],
  video: ['GENERATE_VIDEO', 'REGENERATE_VIDEO'],
  upscale: ['UPSCALE_VIDEO'],
}

function parseCharNames(raw: string | null): string[] {
  if (!raw) return []
  try { return JSON.parse(raw) } catch { return [] }
}

function stageStatus(scene: Scene, stage: SceneStage): StatusType {
  if (stage === 'image') return scene.vertical_image_status !== 'PENDING' ? scene.vertical_image_status : scene.horizontal_image_status
  if (stage === 'video') return scene.vertical_video_status !== 'PENDING' ? scene.vertical_video_status : scene.horizontal_video_status
  return scene.vertical_upscale_status !== 'PENDING' ? scene.vertical_upscale_status : scene.horizontal_upscale_status
}

function stageOutputUrl(scene: Scene, stage: SceneStage): string | null {
  if (stage === 'image') return scene.vertical_image_url || scene.horizontal_image_url
  if (stage === 'video') return scene.vertical_video_url || scene.horizontal_video_url
  return scene.vertical_upscale_url || scene.horizontal_upscale_url
}

function latestRequest(requests: Request[], types: string[]): Request | undefined {
  return requests
    .filter(r => types.includes(r.type))
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))[0]
}

interface SceneDetailSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  scene: Scene | null
  stage: SceneStage
  stageName: string
  characters: Character[]
  requests: Request[]
  review?: SceneReview
  reviewRunning: boolean
  runningMode: 'light' | 'deep' | null
  reviewError?: string | null
  onRunReview: (mode: 'light' | 'deep') => void
  onRetry: () => void
  retrying: boolean
}

export default function SceneDetailSheet({
  open, onOpenChange, scene, stage, stageName, characters, requests, review,
  reviewRunning, runningMode, reviewError, onRunReview, onRetry, retrying,
}: SceneDetailSheetProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState('output')

  if (!scene) return null

  const status = stageStatus(scene, stage)
  const prompt = stage === 'video' ? scene.video_prompt : stage === 'image' ? scene.image_prompt : null
  const outputUrl = stageOutputUrl(scene, stage)
  const castNames = parseCharNames(scene.character_names)
  const refs = characters.filter(c => castNames.includes(c.name))
  const currentRequest = latestRequest(requests, STAGE_TYPES[stage])

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex flex-col p-0 gap-0" style={{ width: 'min(1100px, 92vw)', maxWidth: 'none' }}>
        <div className="px-6 pt-5 pb-3.5" style={{ borderBottom: '1px solid var(--border)' }}>
          <SheetHeader className="p-0 gap-2">
            <SheetTitle>{t('sceneSheet.sceneTitle', { n: scene.display_order + 1 })}</SheetTitle>
            <SheetDescription>{t('sceneSheet.subtitle', { stage: stageName.toLowerCase(), id: scene.id })}</SheetDescription>
          </SheetHeader>
          <div className="flex items-center gap-2.5 mt-3">
            <span
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[10px] tracking-widest border"
              style={{ borderColor: 'var(--border)', color: STATUS_COLORS[status] }}
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: STATUS_COLORS[status] }} />
              {statusLabel(t, status)}
            </span>
            <span className="text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
              {t('sceneSheet.retryOf3', { n: currentRequest?.retry_count ?? 0 })}
            </span>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="output">{t('sceneSheet.tab.output')}</TabsTrigger>
              <TabsTrigger value="review">{t('sceneSheet.tab.review')}</TabsTrigger>
              <TabsTrigger value="stages">{t('sceneSheet.tab.stages')}</TabsTrigger>
            </TabsList>

            <TabsContent value="output">
              <div className="flex flex-col gap-5 pt-4">
                {status === 'FAILED' && currentRequest?.error_message && (
                  <div className="rounded-md p-3.5" style={{ border: '1px solid var(--red)', borderLeftWidth: 3, background: 'rgba(239,68,68,.07)' }}>
                    <div className="text-[10px] tracking-widest mb-2" style={{ color: 'var(--red)' }}>{t('sceneSheet.error')}</div>
                    <pre className="m-0 text-[11px] leading-relaxed whitespace-pre-wrap" style={{ color: '#fca5a5', fontFamily: 'inherit' }}>
                      {currentRequest.error_message}
                    </pre>
                  </div>
                )}

                <div>
                  <div className="text-[10px] tracking-widest mb-2" style={{ color: 'var(--muted)' }}>{t('sceneSheet.prompt')}</div>
                  {prompt ? (
                    <pre className="m-0 p-3.5 rounded-md text-xs leading-relaxed whitespace-pre-wrap" style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)', fontFamily: 'inherit' }}>
                      {prompt}
                    </pre>
                  ) : (
                    <div className="text-xs" style={{ color: 'var(--muted)' }}>
                      {stage === 'upscale' ? t('sceneSheet.noPromptUpscale') : t('sceneSheet.noPromptSet')}
                    </div>
                  )}
                </div>

                <div>
                  <div className="text-[10px] tracking-widest mb-2" style={{ color: 'var(--muted)' }}>{t('sceneSheet.output')}</div>
                  <div className="relative flex items-center justify-center overflow-hidden rounded-lg" style={{ aspectRatio: '16/9', background: 'var(--surface)', border: '1px solid var(--border)' }}>
                    {outputUrl ? (
                      stage === 'image' ? (
                        <img src={outputUrl} alt={t('sceneSheet.output')} className="w-full h-full object-cover" />
                      ) : (
                        <video src={outputUrl} controls className="w-full h-full object-cover" />
                      )
                    ) : (
                      <span className="text-[11px] tracking-widest" style={{ color: 'var(--muted)' }}>
                        {status === 'FAILED' ? t('sceneSheet.noOutputFailed') : t('sceneSheet.notGenerated')}
                      </span>
                    )}
                  </div>
                </div>

                {refs.length > 0 && (
                  <div>
                    <div className="text-[10px] tracking-widest mb-2" style={{ color: 'var(--muted)' }}>{t('sceneSheet.referenceMediaUsed', { n: refs.length })}</div>
                    <div className="grid gap-2.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
                      {refs.map(r => (
                        <div key={r.id} className="rounded-md overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--surface)' }}>
                          <div className="flex items-center justify-center" style={{ aspectRatio: '1/1', background: 'var(--bg)' }}>
                            {r.reference_image_url ? (
                              <img src={r.reference_image_url} alt={r.name} className="w-full h-full object-cover" />
                            ) : (
                              <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('sceneSheet.noRef')}</span>
                            )}
                          </div>
                          <div className="px-2.5 py-2 flex flex-col gap-0.5">
                            <span className="text-[11px]">{r.name}</span>
                            <span className="text-[9px] tracking-wide uppercase" style={{ color: 'var(--muted)' }}>{r.entity_type}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div>
                  <div className="text-[10px] tracking-widest mb-2" style={{ color: 'var(--muted)' }}>{t('sceneSheet.generationMetadata')}</div>
                  <Table>
                    <TableBody>
                      {[
                        [t('sceneSheet.meta.status'), statusLabel(t, status)],
                        [t('sceneSheet.meta.mediaId'), currentRequest?.media_id ?? t('common.dash')],
                        [t('sceneSheet.meta.outputUrl'), currentRequest?.output_url ?? t('common.dash')],
                        [t('sceneSheet.meta.retryCount'), t('sceneSheet.retryCountOf3', { n: currentRequest?.retry_count ?? 0 })],
                        [t('sceneSheet.meta.createdAt'), currentRequest?.created_at ?? t('common.dash')],
                        [t('sceneSheet.meta.updatedAt'), currentRequest?.updated_at ?? t('common.dash')],
                      ].map(([k, v]) => (
                        <TableRow key={k}>
                          <TableCell className="w-[220px]">
                            <span className="text-[10px] tracking-wide uppercase" style={{ color: 'var(--muted)' }}>{k}</span>
                          </TableCell>
                          <TableCell>
                            <span className="text-[11px] break-all" style={{ color: 'var(--text)' }}>{v}</span>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="review">
              <div className="flex flex-col gap-5 pt-4">
                <div className="flex items-center gap-3">
                  <Button variant="outline" size="sm" disabled={reviewRunning} onClick={() => onRunReview('light')}>{t('sceneSheet.runLight')}</Button>
                  <Button size="sm" disabled={reviewRunning} onClick={() => onRunReview('deep')}>{t('sceneSheet.runDeep')}</Button>
                  {review && !reviewRunning && (
                    <span className="text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
                      {t('sceneSheet.lastRun', { fps: review.fps_used, frames: review.frames_analyzed })}
                    </span>
                  )}
                </div>

                {reviewError && !reviewRunning && (
                  <div className="text-[11px]" style={{ color: 'var(--red)' }}>{reviewError}</div>
                )}

                {reviewRunning && (
                  <div className="rounded-md p-4" style={{ border: '1px solid var(--yellow)', background: 'rgba(245,158,11,.06)' }}>
                    <div className="flex items-center gap-2.5 text-[11px] tracking-wide" style={{ color: 'var(--yellow)' }}>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--yellow)', animation: 'pulse 1.2s ease-in-out infinite' }} />
                      {t('sceneSheet.analyzing', { mode: runningMode === 'deep' ? t('sceneSheet.modeDeep') : t('sceneSheet.modeLight') })}
                    </div>
                    <div className="mt-3 text-[10px]" style={{ color: 'var(--muted)' }}>
                      {t('sceneSheet.analyzingBody')}
                    </div>
                  </div>
                )}

                {review && !reviewRunning && (
                  <div className="flex flex-col gap-5">
                    <div
                      className="rounded-md p-4 flex items-center justify-between gap-5"
                      style={{ border: '1px solid var(--border)', borderLeft: `3px solid ${VERDICT_COLORS[review.verdict] ?? 'var(--muted)'}`, background: 'var(--surface)' }}
                    >
                      <div className="flex flex-col gap-1.5">
                        <span className="text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('sceneSheet.verdict')}</span>
                        <span className="text-2xl tracking-wide uppercase" style={{ color: VERDICT_COLORS[review.verdict] ?? 'var(--text)' }}>{review.verdict}</span>
                      </div>
                      <div className="flex flex-col gap-1.5 items-end">
                        <span className="text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('sceneSheet.overall')}</span>
                        <span className="text-2xl" style={{ color: VERDICT_COLORS[review.verdict] ?? 'var(--text)' }}>{review.overall_score.toFixed(1)}</span>
                      </div>
                    </div>

                    <div>
                      <div className="text-[10px] tracking-widest mb-2.5" style={{ color: 'var(--muted)' }}>{t('sceneSheet.dimensionScores')}</div>
                      <div className="rounded-md px-4 py-1.5" style={{ border: '1px solid var(--border)', background: 'var(--surface)' }}>
                        {Object.entries(review.dimensions).map(([key, score]) => (
                          <div key={key} className="grid items-center gap-3.5 py-2" style={{ gridTemplateColumns: '190px 1fr 44px', borderBottom: '1px solid var(--border)' }}>
                            <span className="text-[11px]" style={{ color: 'var(--text)' }}>{dimensionLabel(t, key)}</span>
                            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border)' }}>
                              <div className="h-full rounded-full" style={{ width: `${score * 10}%`, background: scoreColor(score) }} />
                            </div>
                            <span className="text-xs text-right" style={{ color: scoreColor(score) }}>{score.toFixed(1)}</span>
                          </div>
                        ))}
                      </div>
                    </div>

                    {review.errors.length > 0 && (
                      <div>
                        <div className="text-[10px] tracking-widest mb-2.5" style={{ color: 'var(--muted)' }}>{t('sceneSheet.detectedErrors', { n: review.errors.length })}</div>
                        <div className="flex flex-col gap-2">
                          {review.errors.map((e, i) => (
                            <div key={i} className="rounded-md p-3 flex gap-3.5 items-start" style={{ border: '1px solid var(--border)', borderLeft: `3px solid ${SEV_COLORS[e.severity] ?? 'var(--muted)'}`, background: 'var(--surface)' }}>
                              <span className="text-[9px] tracking-widest px-1.5 py-0.5 rounded flex-shrink-0" style={{ color: SEV_COLORS[e.severity] ?? 'var(--muted)' }}>{e.severity}</span>
                              <span className="text-[11px] flex-shrink-0" style={{ color: 'var(--muted)', width: 92 }}>{e.time_range}</span>
                              <span className="text-[11px] leading-relaxed" style={{ color: 'var(--text)' }}>{e.description}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {review.usable_segments.length > 0 && (
                      <div>
                        <div className="text-[10px] tracking-widest mb-2.5" style={{ color: 'var(--muted)' }}>{t('sceneSheet.usableSegments')}</div>
                        <div className="flex flex-wrap gap-2">
                          {review.usable_segments.map((s, i) => (
                            <span key={i} className="text-[11px] px-2.5 py-1 rounded" style={{ color: 'var(--green)', border: '1px solid var(--border)', background: 'var(--surface)' }}>
                              {s.time_range} · {s.score.toFixed(1)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    <div>
                      <div className="text-[10px] tracking-widest mb-2.5" style={{ color: 'var(--muted)' }}>{t('sceneSheet.fixGuide')}</div>
                      <pre className="m-0 p-3.5 rounded-md text-xs leading-relaxed whitespace-pre-wrap" style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)', fontFamily: 'inherit' }}>
                        {review.fix_guide}
                      </pre>
                    </div>
                  </div>
                )}

                {!review && !reviewRunning && (
                  <div className="rounded-md p-7 text-center text-xs" style={{ border: '1px dashed var(--border)', color: 'var(--muted)' }}>
                    {t('sceneSheet.noReview')}
                  </div>
                )}
              </div>
            </TabsContent>

            <TabsContent value="stages">
              <div className="pt-4">
                <Accordion type="multiple" defaultValue={[stage]}>
                  {(['image', 'video', 'upscale'] as const).map(sk => {
                    const st = stageStatus(scene, sk)
                    const req = latestRequest(requests, STAGE_TYPES[sk])
                    return (
                      <AccordionItem key={sk} value={sk}>
                        <AccordionTrigger>
                          <div className="flex items-center gap-3 w-full">
                            <span className="w-1.5 h-1.5 rounded-full" style={{ background: STATUS_COLORS[st] }} />
                            <span className="text-[11px] tracking-widest uppercase">{stageLowerLabel(t, sk)}</span>
                            <span className="text-[10px] ml-auto" style={{ color: 'var(--muted)' }}>{statusLabel(t, st)}</span>
                          </div>
                        </AccordionTrigger>
                        <AccordionContent>
                          <div className="flex flex-col gap-2 pl-1">
                            <div className="flex gap-6 text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
                              <span>{t('sceneSheet.stageCreated', { date: req?.created_at ?? t('common.dash') })}</span>
                              <span>{t('sceneSheet.stageUpdated', { date: req?.updated_at ?? t('common.dash') })}</span>
                              <span>{t('sceneSheet.stageRetries', { n: req?.retry_count ?? 0 })}</span>
                            </div>
                            {req?.error_message && (
                              <pre className="m-0 text-[11px] leading-relaxed whitespace-pre-wrap" style={{ color: '#fca5a5', fontFamily: 'inherit' }}>
                                {req.error_message}
                              </pre>
                            )}
                            {!req && (
                              <div className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('sceneSheet.noRequestYet')}</div>
                            )}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )
                  })}
                </Accordion>
              </div>
            </TabsContent>
          </Tabs>
        </div>

        <SheetFooter className="flex-row items-center gap-2.5 p-3" style={{ borderTop: '1px solid var(--border)' }}>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>{t('sceneSheet.close')}</Button>
          <span className="ml-auto" />
          <Button
            variant="outline"
            size="sm"
            disabled={!prompt}
            onClick={() => prompt && navigator.clipboard.writeText(prompt)}
          >
            {t('sceneSheet.copyPrompt')}
          </Button>
          <Button variant="outline" size="sm" disabled={retrying} onClick={onRetry}>
            {retrying ? t('sceneSheet.retrying') : t('sceneSheet.retryStage')}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
