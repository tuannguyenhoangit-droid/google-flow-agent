import { useState, useEffect, useCallback } from 'react'
import { fetchAPI } from '../api/client'
import type { Project, Video, Scene } from '../types'
import VideoGallery from '../components/gallery/VideoGallery'
import { useTranslation } from '../i18n/useTranslation'

export default function GalleryPage() {
  const { t } = useTranslation()
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [videos, setVideos] = useState<Video[]>([])
  const [scenes, setScenes] = useState<(Scene & { videoTitle: string })[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchAPI<Project[]>('/api/projects')
      .then(ps => {
        const active = ps.filter(p => p.status !== 'DELETED')
        setProjects(active)
        if (active.length > 0) setSelectedProject(active[0].id)
      })
      .catch(console.error)
  }, [])

  const loadProjectMedia = useCallback(async (projectId: string) => {
    setLoading(true)
    const vids = await fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`)
    setVideos(vids)
    const sceneLists = await Promise.all(vids.map(v => fetchAPI<Scene[]>(`/api/scenes?video_id=${v.id}`)))
    const merged = vids.flatMap((v, i) => sceneLists[i].map(s => ({ ...s, videoTitle: v.title })))
    setScenes(merged)
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!selectedProject) return
    Promise.resolve().then(() => loadProjectMedia(selectedProject).catch(console.error))
  }, [selectedProject, loadProjectMedia])

  return (
    <div className="flex flex-col gap-4">
      {/* Filters */}
      <div className="flex gap-3 flex-wrap items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--muted)' }}>{t('gallery.projectLabel')}</label>
          <select
            value={selectedProject}
            onChange={e => setSelectedProject(e.target.value)}
            className="text-xs px-2 py-1.5 rounded outline-none"
            style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
          >
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <span className="text-[11px] ml-auto" style={{ color: 'var(--muted)' }}>
          {t('gallery.count', { videos: videos.length, scenes: scenes.length })}
        </span>
      </div>

      {loading ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('gallery.loading')}</div>
      ) : (
        <VideoGallery scenes={scenes} />
      )}
    </div>
  )
}
