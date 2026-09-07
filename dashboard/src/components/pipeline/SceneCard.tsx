import { Card, CardHeader, CardTitle, CardDescription, CardAction, CardContent } from '../ui/card'
import type { Scene, StatusType } from '../../types'
import { useTranslation } from '../../i18n/useTranslation'
import { statusLabel, stageTitleLabel } from '../../i18n/labels'

export type SceneStage = 'image' | 'video' | 'upscale'

interface SceneCardProps {
  scene: Scene
  stage: SceneStage
  retries: number
  verdict?: string
  onClick: () => void
}

const STATUS_COLORS: Record<StatusType, string> = {
  COMPLETED: 'var(--green)',
  PROCESSING: 'var(--yellow)',
  PENDING: 'var(--muted)',
  FAILED: 'var(--red)',
}

const STATUS_TINT: Record<StatusType, string> = {
  COMPLETED: 'linear-gradient(135deg, rgba(34,197,94,.07), rgba(59,130,246,.05))',
  PROCESSING: 'linear-gradient(135deg, rgba(245,158,11,.10), rgba(245,158,11,.02))',
  FAILED: 'linear-gradient(135deg, rgba(239,68,68,.10), rgba(239,68,68,.02))',
  PENDING: 'none',
}

const VERDICT_COLORS: Record<string, string> = {
  excellent: 'var(--green)',
  good: 'var(--green)',
  acceptable: 'var(--yellow)',
  poor: 'var(--red)',
  unusable: 'var(--red)',
}

function getStageStatus(scene: Scene, stage: SceneStage): StatusType {
  if (stage === 'image') return scene.vertical_image_status !== 'PENDING' ? scene.vertical_image_status : scene.horizontal_image_status
  if (stage === 'video') return scene.vertical_video_status !== 'PENDING' ? scene.vertical_video_status : scene.horizontal_video_status
  return scene.vertical_upscale_status !== 'PENDING' ? scene.vertical_upscale_status : scene.horizontal_upscale_status
}

function getThumbUrl(scene: Scene): string | null {
  return scene.vertical_image_url || scene.horizontal_image_url
}

export default function SceneCard({ scene, stage, retries, verdict, onClick }: SceneCardProps) {
  const { t } = useTranslation()
  const status = getStageStatus(scene, stage)
  const thumbUrl = getThumbUrl(scene)
  const prompt = stage === 'video' ? scene.video_prompt : (scene.image_prompt ?? scene.prompt)

  return (
    <button onClick={onClick} className="text-left w-full">
      <Card className="gap-3 py-4 h-full">
        <CardHeader>
          <CardTitle>
            <span className="text-sm tracking-wide">{t('sceneCard.scene', { n: scene.display_order + 1 })}</span>
          </CardTitle>
          <CardDescription>
            <span className="text-[10px] tracking-wide">{stageTitleLabel(t, stage)} · {scene.duration ? `${scene.duration}s` : t('sceneCard.still')}</span>
          </CardDescription>
          <CardAction>
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] tracking-widest border"
              style={{ borderColor: 'var(--border)', color: STATUS_COLORS[status] }}
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: STATUS_COLORS[status] }} />
              {statusLabel(t, status)}
            </span>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div
            className="relative flex items-center justify-center overflow-hidden rounded-md"
            style={{ aspectRatio: '16/9', background: 'var(--surface)', border: '1px solid var(--border)' }}
          >
            {thumbUrl ? (
              <img src={thumbUrl} alt={t('sceneCard.scene', { n: scene.display_order + 1 })} className="w-full h-full object-cover" />
            ) : (
              <>
                <div className="absolute inset-0" style={{ background: STATUS_TINT[status] }} />
                <span className="relative text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>
                  {status === 'PENDING' ? t('sceneCard.notGenerated') : status === 'FAILED' ? t('sceneCard.noOutput') : t('sceneCard.noPreview')}
                </span>
              </>
            )}
          </div>
          {prompt && (
            <p
              className="mt-3 text-[11px] leading-snug overflow-hidden"
              style={{ color: 'var(--muted)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
            >
              {prompt}
            </p>
          )}
          <div className="flex items-center gap-2 mt-3 text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
            <span>{t('sceneCard.retries', { n: retries })}</span>
            {verdict && (
              <span className="ml-auto uppercase" style={{ color: VERDICT_COLORS[verdict] ?? 'var(--muted)' }}>
                {verdict}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </button>
  )
}
