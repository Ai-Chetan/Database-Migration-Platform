import { Construction } from 'lucide-react'
import { PageHeader } from '@/components/common'

export function ComingSoon({ title }: { title: string }) {
  return (
    <div>
      <PageHeader title={title} />
      <div className="flex flex-col items-center justify-center rounded border border-dashed border-border bg-white py-24 text-center">
        <Construction className="mb-3 h-8 w-8 text-text-tertiary" />
        <p className="text-body text-text-secondary">This page is being built in an upcoming part.</p>
      </div>
    </div>
  )
}
