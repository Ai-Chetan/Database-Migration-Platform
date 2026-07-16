import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, BookOpen, Star } from 'lucide-react'
import { knowledgeApi } from '@/api/knowledge'
import { Engine } from '@/types'
import { PageHeader, Input, Select, Badge, Card, EmptyState, SkeletonCard } from '@/components/common'
import { ENGINE_LABELS } from '@/utils/meta'
import { useDebounce } from '@/hooks/useDebounce'

const ENGINES: Engine[] = ['mysql', 'postgresql', 'sqlite', 's3', 'azure', 'gcs', 'kafka', 'rest_api', 'file']

export default function KnowledgeBase() {
  const [search, setSearch] = useState('')
  const [sourceEngine, setSourceEngine] = useState('')
  const [targetEngine, setTargetEngine] = useState('')
  const debouncedSearch = useDebounce(search, 300)

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['knowledge', 'entries', debouncedSearch, sourceEngine, targetEngine],
    queryFn: () =>
      knowledgeApi.list({
        search: debouncedSearch || undefined,
        source_engine: sourceEngine || undefined,
        target_engine: targetEngine || undefined,
      }),
  })

  return (
    <div>
      <PageHeader title="Knowledge Base" description="Lessons learned and patterns discovered from past migrations." />

      <div className="mb-6 flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-tertiary" />
          <Input className="pl-9" placeholder="Search entries…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Select className="sm:w-48" value={sourceEngine} onChange={(e) => setSourceEngine(e.target.value)}>
          <option value="">Any source engine</option>
          {ENGINES.map((e) => (
            <option key={e} value={e}>{ENGINE_LABELS[e]}</option>
          ))}
        </Select>
        <Select className="sm:w-48" value={targetEngine} onChange={(e) => setTargetEngine(e.target.value)}>
          <option value="">Any target engine</option>
          {ENGINES.map((e) => (
            <option key={e} value={e}>{ENGINE_LABELS[e]}</option>
          ))}
        </Select>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : entries.length === 0 ? (
        <EmptyState icon={BookOpen} title="No entries found" description="Try a different search or filter." />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {entries.map((entry) => (
            <Card key={entry.id} hoverable>
              <div className="mb-2 flex items-center justify-between">
                <Badge tone="info">{entry.entry_type}</Badge>
                <span className="flex items-center gap-1 text-tiny text-text-tertiary">
                  <Star className="h-3 w-3 fill-current text-warning" /> {entry.usefulness_score.toFixed(1)}
                </span>
              </div>
              <p className="text-body font-medium text-text-primary">{entry.title}</p>
              <p className="mt-1 text-tiny text-text-tertiary">
                {ENGINE_LABELS[entry.source_engine]} → {ENGINE_LABELS[entry.target_engine]}
              </p>
              {entry.tags.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {entry.tags.map((tag) => (
                    <Badge key={tag} tone="neutral">{tag}</Badge>
                  ))}
                </div>
              )}
              <p className="mt-3 text-tiny text-text-tertiary">Referenced {entry.reference_count} times</p>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
