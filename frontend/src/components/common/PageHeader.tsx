import React from 'react'

interface PageHeaderProps {
  title: string
  description?: React.ReactNode
  actions?: React.ReactNode
  breadcrumb?: React.ReactNode
}

export function PageHeader({ title, description, actions, breadcrumb }: PageHeaderProps) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        {breadcrumb && <div className="mb-1 text-small text-text-tertiary">{breadcrumb}</div>}
        <h1 className="text-h2 text-text-primary">{title}</h1>
        {description && <p className="mt-1 text-body text-text-secondary">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-3">{actions}</div>}
    </div>
  )
}
