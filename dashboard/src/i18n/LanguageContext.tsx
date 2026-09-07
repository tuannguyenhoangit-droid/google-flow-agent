import { useCallback, useState, type ReactNode } from 'react'
import { LANGS, translations, type Lang, type TranslationKey } from './translations'
import { LanguageContext } from './context'

const STORAGE_KEY = 'fk-lang'

function detectDefaultLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && (LANGS as readonly string[]).includes(stored)) return stored as Lang
  } catch {
    // localStorage unavailable — fall through to browser language detection
  }
  const nav = (navigator.language || 'en').slice(0, 2).toLowerCase()
  return (LANGS as readonly string[]).includes(nav) ? (nav as Lang) : 'en'
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectDefaultLang)

  const setLang = useCallback((next: Lang) => {
    setLangState(next)
    try { localStorage.setItem(STORAGE_KEY, next) } catch {
      // localStorage unavailable — language choice just won't persist across reloads
    }
  }, [])

  const t = useCallback((key: TranslationKey, params?: Record<string, string | number>) => {
    const template = translations[lang][key] ?? translations.en[key] ?? key
    if (!params) return template
    return Object.entries(params).reduce(
      (str, [name, value]) => str.replaceAll(`{${name}}`, String(value)),
      template
    )
  }, [lang])

  return (
    <LanguageContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LanguageContext.Provider>
  )
}
