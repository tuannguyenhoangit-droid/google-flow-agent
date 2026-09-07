import { createContext } from 'react'
import type { Lang, TranslationKey } from './translations'

export interface LanguageContextValue {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (key: TranslationKey, params?: Record<string, string | number>) => string
}

export const LanguageContext = createContext<LanguageContextValue | null>(null)
