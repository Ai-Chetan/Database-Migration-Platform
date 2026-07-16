import React from 'react'
import { Database, HardDrive, Cloud, Radio, Globe, FileText } from 'lucide-react'
import { Engine } from '@/types'
import { ENGINE_LABELS } from '@/utils/meta'
import { cn } from '@/utils/cn'

const ENGINE_ICONS: Record<Engine, React.ElementType> = {
  mysql: Database,
  postgresql: Database,
  sqlite: HardDrive,
  s3: Cloud,
  azure: Cloud,
  gcs: Cloud,
  kafka: Radio,
  rest_api: Globe,
  file: FileText,
}

const ENGINE_COLORS: Record<Engine, string> = {
  mysql: 'text-orange-600 bg-orange-50',
  postgresql: 'text-blue-700 bg-blue-50',
  sqlite: 'text-slate-600 bg-slate-100',
  s3: 'text-amber-600 bg-amber-50',
  azure: 'text-sky-600 bg-sky-50',
  gcs: 'text-red-600 bg-red-50',
  kafka: 'text-purple-600 bg-purple-50',
  rest_api: 'text-emerald-600 bg-emerald-50',
  file: 'text-slate-600 bg-slate-100',
}

export function EngineIcon({
  engine,
  size = 'md',
  showLabel = false,
}: {
  engine: Engine
  size?: 'sm' | 'md' | 'lg'
  showLabel?: boolean
}) {
  const Icon = ENGINE_ICONS[engine]
  const dims = { sm: 'h-6 w-6', md: 'h-8 w-8', lg: 'h-10 w-10' }[size]
  const iconDims = { sm: 'h-3.5 w-3.5', md: 'h-4 w-4', lg: 'h-5 w-5' }[size]

  return (
    <div className="inline-flex items-center gap-2">
      <div className={cn('flex items-center justify-center rounded', dims, ENGINE_COLORS[engine])}>
        <Icon className={iconDims} />
      </div>
      {showLabel && <span className="text-body text-text-primary">{ENGINE_LABELS[engine]}</span>}
    </div>
  )
}
