import { useState, useRef, useEffect } from 'react'
import { useTranslation } from '../../i18n/useTranslation'

interface EditableTextProps {
  value: string
  onSave: (newValue: string) => void
  multiline?: boolean
  className?: string
}

export default function EditableText({ value, onSave, multiline = false, className = '' }: EditableTextProps) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const inputRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null)

  useEffect(() => {
    setDraft(value)
  }, [value])

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  function handleSave() {
    setEditing(false)
    if (draft !== value) onSave(draft)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      setDraft(value)
      setEditing(false)
    } else if (e.key === 'Enter' && !multiline) {
      handleSave()
    } else if (e.key === 'Enter' && multiline && !e.shiftKey) {
      // Shift+Enter = newline, Enter alone = save
      handleSave()
    }
  }

  if (!editing) {
    return (
      <span
        className={`cursor-pointer hover:opacity-70 transition-opacity ${className}`}
        onClick={() => setEditing(true)}
        title={t('editableText.clickToEdit')}
      >
        {value || <span style={{ color: 'var(--muted)' }}>{t('editableText.empty')}</span>}
      </span>
    )
  }

  if (multiline) {
    return (
      <textarea
        ref={inputRef as React.RefObject<HTMLTextAreaElement>}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={handleSave}
        onKeyDown={handleKeyDown}
        rows={4}
        className={`w-full rounded px-2 py-1 text-xs resize-y outline-none ${className}`}
        style={{
          background: 'var(--bg)',
          color: 'var(--text)',
          border: '1px solid var(--accent)',
        }}
      />
    )
  }

  return (
    <input
      ref={inputRef as React.RefObject<HTMLInputElement>}
      type="text"
      value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={handleSave}
      onKeyDown={handleKeyDown}
      className={`w-full rounded px-2 py-1 text-xs outline-none ${className}`}
      style={{
        background: 'var(--bg)',
        color: 'var(--text)',
        border: '1px solid var(--accent)',
      }}
    />
  )
}
