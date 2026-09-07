import { Card, CardHeader, CardTitle, CardDescription, CardAction, CardContent } from '../ui/card'
import { Progress } from '../ui/progress'
import { useTranslation } from '../../i18n/useTranslation'

export type StageKey = 'refs' | 'image' | 'video' | 'upscale'

interface StageNodeProps {
  idx: string
  name: string
  subtitle: string
  done: number
  processing: number
  failed: number
  pending: number
  total: number
  isActive: boolean
  onClick: () => void
}

export default function StageNode({ idx, name, subtitle, done, processing, failed, pending, total, isActive, onClick }: StageNodeProps) {
  const { t } = useTranslation()
  const pct = total === 0 ? 0 : Math.round((done / total) * 100)
  const accent = isActive ? 'var(--accent)' : 'var(--border)'

  return (
    <button onClick={onClick} className="flex-1 min-w-0 text-left">
      <Card className="gap-3 py-4">
        <div style={{ height: 2, margin: '-16px 0 0', background: accent }} />
        <CardHeader>
          <CardTitle>
            <span className="text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{idx}</span>
            <span className="ml-2 text-sm tracking-wide uppercase">{name}</span>
          </CardTitle>
          <CardDescription>
            <span className="text-xs">{subtitle}</span>
          </CardDescription>
          <CardAction>
            <span className="text-lg tracking-tight" style={{ color: isActive ? 'var(--accent)' : 'var(--text)' }}>{done}</span>
            <span className="text-xs" style={{ color: 'var(--muted)' }}>/{total}</span>
          </CardAction>
        </CardHeader>
        <CardContent>
          <Progress value={pct} />
          <div className="flex flex-wrap gap-x-3 gap-y-1.5 mt-3 text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5" style={{ background: 'var(--green)' }} />{done} {t('stageNode.done')}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5" style={{ background: 'var(--yellow)' }} />{processing} {t('stageNode.proc')}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5" style={{ background: 'var(--red)' }} />{failed} {t('stageNode.fail')}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5" style={{ background: 'var(--border)' }} />{pending} {t('stageNode.pend')}
            </span>
          </div>
        </CardContent>
      </Card>
    </button>
  )
}
