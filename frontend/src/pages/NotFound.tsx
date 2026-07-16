import { Link } from 'react-router-dom'
export default function NotFound() {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
      <h2 className="text-h3 text-text-primary">404 — Page not found</h2>
      <Link to="/app/dashboard" className="mt-3 text-body text-action hover:underline">
        Back to dashboard
      </Link>
    </div>
  )
}
