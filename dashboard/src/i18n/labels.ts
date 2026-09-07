import type { TranslationKey } from './translations'
import type { StatusType, ChainType, ProjectStatus } from '../types'

type T = (key: TranslationKey, params?: Record<string, string | number>) => string

export function statusLabel(t: T, status: StatusType): string {
  return t(`common.status.${status.toLowerCase()}` as TranslationKey)
}

export function stateLabel(t: T, state: 'RUNNING' | 'QUEUED' | 'IDLE' | 'COMPLETED'): string {
  if (state === 'COMPLETED') return t('common.status.completed')
  return t(`common.state.${state.toLowerCase()}` as TranslationKey)
}

export function projectStatusLabel(t: T, status: ProjectStatus): string {
  return t(`common.projectStatus.${status.toLowerCase()}` as TranslationKey)
}

export function chainLabel(t: T, chain: ChainType): string {
  return t(`common.chain.${chain.toLowerCase()}` as TranslationKey)
}

export function stageTitleLabel(t: T, stage: 'refs' | 'image' | 'video' | 'upscale'): string {
  return t(`common.stageTitle.${stage}` as TranslationKey)
}

export function stageLowerLabel(t: T, stage: 'refs' | 'image' | 'video' | 'upscale'): string {
  return t(`common.stageLower.${stage}` as TranslationKey)
}

export function dimensionLabel(t: T, key: string): string {
  const k = `common.dimension.${key}` as TranslationKey
  const label = t(k)
  return label === k ? key.replace(/_/g, ' ') : label
}
