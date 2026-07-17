import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Search, CornerDownLeft, ArrowUp, ArrowDown } from 'lucide-react'
import { useUIStore } from '@/store/ui'
import { useAuthStore } from '@/store/auth'
import { hasPermission } from '@/utils/permissions'
import { NAV_GROUPS } from '@/utils/navigation'
import { cn } from '@/utils/cn'

interface Flat {
  to: string
  label: string
  group: string
  icon: React.ElementType
  permission: string
  keywords?: string
}

function useFilteredCommands(query: string): Flat[] {
  const role = useAuthStore((s) => s.user?.role)

  const allowed = useMemo<Flat[]>(
    () =>
      NAV_GROUPS.flatMap((g) => g.items.map((item) => ({ ...item, group: g.label }))).filter((item) =>
        hasPermission(role, item.permission)
      ),
    [role]
  )

  return useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return allowed
    return allowed.filter((item) => {
      const haystack = `${item.label} ${item.group} ${item.keywords ?? ''}`.toLowerCase()
      return haystack.includes(q)
    })
  }, [allowed, query])
}

export function CommandPalette() {
  const isOpen = useUIStore((s) => s.commandPaletteOpen)
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen)
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const results = useFilteredCommands(query)

  // Global Cmd+K / Ctrl+K listener
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isK = e.key.toLowerCase() === 'k'
      if ((e.metaKey || e.ctrlKey) && isK) {
        e.preventDefault()
        setOpen(!isOpen)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [isOpen, setOpen])

  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setActiveIndex(0)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [isOpen])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  function go(item: Flat) {
    navigate(item.to)
    setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      setOpen(false)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const item = results[activeIndex]
      if (item) go(item)
    }
  }

  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[12vh]">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
            onClick={() => setOpen(false)}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -8 }}
            transition={{ duration: 0.15, ease: 'easeOut' }}
            className="relative w-full max-w-xl overflow-hidden rounded-lg border border-border bg-white shadow-lg"
            onKeyDown={onKeyDown}
          >
            <div className="flex items-center gap-3 border-b border-border px-4 py-3">
              <Search className="h-4 w-4 shrink-0 text-text-tertiary" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Jump to a page or action…"
                className="w-full bg-transparent text-body text-text-primary placeholder:text-text-tertiary focus:outline-none"
              />
              <kbd className="hidden shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-tiny text-text-tertiary sm:block">
                esc
              </kbd>
            </div>

            <div className="max-h-80 overflow-y-auto scrollbar-thin py-2">
              {results.length === 0 ? (
                <p className="px-4 py-6 text-center text-small text-text-tertiary">
                  No matches for "{query}"
                </p>
              ) : (
                results.map((item, idx) => (
                  <button
                    key={item.to}
                    onClick={() => go(item)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={cn(
                      'flex w-full items-center gap-3 px-4 py-2.5 text-left text-small transition-colors',
                      idx === activeIndex ? 'bg-action/10 text-action' : 'text-text-primary'
                    )}
                  >
                    <item.icon className="h-4 w-4 shrink-0" />
                    <span className="flex-1 truncate font-medium">{item.label}</span>
                    <span className="shrink-0 text-tiny text-text-tertiary">{item.group}</span>
                  </button>
                ))
              )}
            </div>

            <div className="flex items-center gap-4 border-t border-border bg-surface px-4 py-2 text-tiny text-text-tertiary">
              <span className="flex items-center gap-1">
                <ArrowUp className="h-3 w-3" /> <ArrowDown className="h-3 w-3" /> navigate
              </span>
              <span className="flex items-center gap-1">
                <CornerDownLeft className="h-3 w-3" /> select
              </span>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body
  )
}
