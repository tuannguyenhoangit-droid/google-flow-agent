// Enums
export type RequestType = 'GENERATE_IMAGE' | 'REGENERATE_IMAGE' | 'EDIT_IMAGE' | 'GENERATE_VIDEO' | 'REGENERATE_VIDEO' | 'GENERATE_VIDEO_REFS' | 'UPSCALE_VIDEO' | 'GENERATE_CHARACTER_IMAGE' | 'REGENERATE_CHARACTER_IMAGE' | 'EDIT_CHARACTER_IMAGE'
export type StatusType = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
export type Orientation = 'VERTICAL' | 'HORIZONTAL'
export type ChainType = 'ROOT' | 'CONTINUATION' | 'INSERT'
export type EntityType = 'character' | 'location' | 'creature' | 'visual_asset' | 'generic_troop' | 'faction'
export type ProjectStatus = 'ACTIVE' | 'ARCHIVED' | 'DELETED'

// Models — match the Python models exactly
export interface Project {
  id: string
  name: string
  description: string | null
  story: string | null
  thumbnail_url: string | null
  language: string
  status: ProjectStatus
  user_paygate_tier: string | null
  material: string
  narrator_voice: string | null
  narrator_ref_audio: string | null
  created_at: string
  updated_at: string
}

export interface Character {
  id: string
  name: string
  entity_type: EntityType
  description: string | null
  image_prompt: string | null
  voice_description: string | null
  reference_image_url: string | null
  media_id: string | null
  created_at: string
  updated_at: string
}

export interface Video {
  id: string
  project_id: string
  title: string
  description: string | null
  display_order: number
  status: string
  vertical_url: string | null
  horizontal_url: string | null
  thumbnail_url: string | null
  duration: number | null
  resolution: string | null
  created_at: string
  updated_at: string
}

export interface Scene {
  id: string
  video_id: string
  display_order: number
  prompt: string | null
  image_prompt: string | null
  video_prompt: string | null
  character_names: string | null  // JSON string array
  parent_scene_id: string | null
  chain_type: ChainType
  source: string | null
  vertical_image_url: string | null
  vertical_image_media_id: string | null
  vertical_image_status: StatusType
  vertical_video_url: string | null
  vertical_video_media_id: string | null
  vertical_video_status: StatusType
  vertical_upscale_url: string | null
  vertical_upscale_media_id: string | null
  vertical_upscale_status: StatusType
  horizontal_image_url: string | null
  horizontal_image_media_id: string | null
  horizontal_image_status: StatusType
  horizontal_video_url: string | null
  horizontal_video_media_id: string | null
  horizontal_video_status: StatusType
  horizontal_upscale_url: string | null
  horizontal_upscale_media_id: string | null
  horizontal_upscale_status: StatusType
  narrator_text: string | null
  trim_start: number | null
  trim_end: number | null
  duration: number | null
  created_at: string
  updated_at: string
}

export interface Request {
  id: string
  project_id: string | null
  video_id: string | null
  scene_id: string | null
  character_id: string | null
  type: RequestType
  orientation: Orientation | null
  status: StatusType
  request_id: string | null
  media_id: string | null
  output_url: string | null
  error_message: string | null
  retry_count: number
  created_at: string
  updated_at: string
}

// WebSocket event
export interface WSEvent {
  type: string
  data: Record<string, unknown>
  timestamp: string
}

// AI video review — match agent/models/review.py exactly
export interface DimensionScores {
  character_consistency: number
  prompt_adherence: number
  motion_quality: number
  visual_fidelity: number
  temporal_coherence: number
  composition: number
}

export interface VideoError {
  severity: string // CRITICAL / HIGH / MINOR
  time_range: string
  description: string
}

export interface SegmentScore {
  time_range: string
  score: number
}

export interface SceneReview {
  scene_id: string
  overall_score: number
  verdict: string // excellent / good / acceptable / poor / unusable
  dimensions: DimensionScores
  errors: VideoError[]
  usable_segments: SegmentScore[]
  fix_guide: string
  frames_analyzed: number
  fps_used: number
  has_critical_errors: boolean
}
