import { ShieldOff } from 'lucide-react'
export default function AccessDenied() {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
      <ShieldOff className="mb-3 h-8 w-8 text-error" />
      <h2 className="text-h3 text-text-primary">Access denied</h2>
      <p className="mt-1 text-body text-text-secondary">You don't have permission to view this page.</p>
    </div>
  )
}
