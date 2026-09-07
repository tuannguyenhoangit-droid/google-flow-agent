import { useState, useEffect } from 'react'
import { fetchAPI } from '../api/client'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'

interface HealthResponse {
  status: string
  version: string
  extension_connected: boolean
  ws: {
    connected: boolean
    active_connections: number
    authenticated_connections: number
    connects: number
    disconnects: number
    uptime_s: number | null
  }
}

function useHealthPoll() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [reachable, setReachable] = useState(true)

  useEffect(() => {
    let cancelled = false
    function poll() {
      fetchAPI<HealthResponse>('/health')
        .then(h => { if (!cancelled) { setHealth(h); setReachable(true) } })
        .catch(() => { if (!cancelled) { setHealth(null); setReachable(false) } })
    }
    Promise.resolve().then(poll)
    const id = setInterval(poll, 4000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  return { health, reachable }
}

const STEP_KEYS: { titleKey: TranslationKey; bodyKey: TranslationKey }[] = [
  { titleKey: 'guide.step1.title', bodyKey: 'guide.step1.body' },
  { titleKey: 'guide.step2.title', bodyKey: 'guide.step2.body' },
  { titleKey: 'guide.step3.title', bodyKey: 'guide.step3.body' },
  { titleKey: 'guide.step4.title', bodyKey: 'guide.step4.body' },
]

const TROUBLE_KEYS: { problemKey: TranslationKey; solutionKey: TranslationKey }[] = [
  { problemKey: 'guide.trouble1.problem', solutionKey: 'guide.trouble1.solution' },
  { problemKey: 'guide.trouble2.problem', solutionKey: 'guide.trouble2.solution' },
  { problemKey: 'guide.trouble3.problem', solutionKey: 'guide.trouble3.solution' },
  { problemKey: 'guide.trouble4.problem', solutionKey: 'guide.trouble4.solution' },
  { problemKey: 'guide.trouble5.problem', solutionKey: 'guide.trouble5.solution' },
  { problemKey: 'guide.trouble6.problem', solutionKey: 'guide.trouble6.solution' },
]

export default function GuidePage() {
  const { t } = useTranslation()
  const { health, reachable } = useHealthPoll()

  return (
    <div className="flex flex-col gap-5 max-w-3xl">
      <div>
        <h1 className="m-0 text-lg font-semibold" style={{ color: 'var(--text)' }}>{t('guide.title')}</h1>
        <p className="text-[11px] mt-1" style={{ color: 'var(--muted)' }}>{t('guide.intro')}</p>
      </div>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('guide.status.title')}</CardTitle>
          <CardDescription className="text-[11px]">{t('guide.status.desc')}</CardDescription>
        </CardHeader>
        <CardContent>
          {!reachable ? (
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--red)' }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--red)' }} />
              {t('guide.status.unreachable')}
            </div>
          ) : !health ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('guide.status.checking')}</div>
          ) : (
            <div className="flex flex-wrap gap-6">
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--green)' }} />
                <span className="text-xs" style={{ color: 'var(--text)' }}>{t('guide.status.agentRunning')}</span>
                <Badge variant="outline">v{health.version}</Badge>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: health.extension_connected ? 'var(--green)' : 'var(--red)' }} />
                <span className="text-xs" style={{ color: health.extension_connected ? 'var(--green)' : 'var(--red)' }}>
                  {health.extension_connected ? t('guide.status.extensionConnected') : t('guide.status.extensionDisconnected')}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: health.ws.authenticated_connections > 0 ? 'var(--green)' : 'var(--muted)' }} />
                <span className="text-xs" style={{ color: 'var(--muted)' }}>
                  {t('guide.status.ws', { active: health.ws.active_connections, authenticated: health.ws.authenticated_connections })}
                </span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        {STEP_KEYS.map((s, i) => (
          <Card key={s.titleKey} className="py-4">
            <CardContent>
              <div className="flex gap-3.5">
                <span
                  className="flex-shrink-0 flex items-center justify-center rounded-full text-xs font-semibold"
                  style={{ width: 22, height: 22, background: 'var(--accent)', color: 'var(--bg)' }}
                >
                  {i + 1}
                </span>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-semibold" style={{ color: 'var(--text)' }}>{t(s.titleKey)}</span>
                  <span className="text-[11px] leading-relaxed" style={{ color: 'var(--muted)' }}>{t(s.bodyKey)}</span>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('guide.trouble.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-3">
            {TROUBLE_KEYS.map(tr => (
              <div key={tr.problemKey} className="flex flex-col gap-0.5 pb-3" style={{ borderBottom: '1px solid var(--border)' }}>
                <span className="text-xs font-semibold" style={{ color: 'var(--text)' }}>{t(tr.problemKey)}</span>
                <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{t(tr.solutionKey)}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
