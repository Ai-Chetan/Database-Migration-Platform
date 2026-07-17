import React from 'react'
import { motion } from 'framer-motion'

interface Node {
  x: number
  y: number
  label: string
  sub: string
}

const NODES: Node[] = [
  { x: 40, y: 90, label: 'mysql_prod', sub: 'source · 12.4M rows' },
  { x: 220, y: 40, label: 'schema_map', sub: '312 columns' },
  { x: 220, y: 150, label: 'mask_pii', sub: '18 rules' },
  { x: 400, y: 90, label: 'postgres_target', sub: 'target · synced' },
]

const EDGES: [number, number][] = [
  [0, 1],
  [0, 2],
  [1, 3],
  [2, 3],
]

const CHIPS = [
  { label: 'rows/sec', value: '48,230', top: '8%', left: '2%', delay: 0 },
  { label: 'active workers', value: '14', top: '68%', left: '4%', delay: 0.4 },
  { label: 'chunks complete', value: '2,184', top: '14%', left: '68%', delay: 0.8 },
  { label: 'error rate', value: '0.02%', top: '72%', left: '66%', delay: 1.2 },
]

/**
 * Signature brand visual: an animated schema-migration flow graph on the dark
 * sidebar surface, with floating live-metric chips. Deliberately not another
 * icon-in-a-circle - this is the one motif that should read as "ours."
 */
export function AuthVisualPanel({ className = '' }: { className?: string }) {
  return (
    <div className={`relative overflow-hidden bg-sidebar-bg ${className}`}>
      {/* faint grid backdrop */}
      <svg className="absolute inset-0 h-full w-full opacity-[0.06]" aria-hidden="true">
        <defs>
          <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M 32 0 L 0 0 0 32" fill="none" stroke="#94A3B8" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>

      {/* radial glow */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(circle at 30% 30%, rgba(37,99,235,0.25) 0%, transparent 55%)',
        }}
      />

      <div className="relative flex h-full flex-col justify-between p-10">
        <div>
          <p className="text-h3 text-white">Every migration, mapped and moving.</p>
          <p className="mt-2 max-w-sm text-small text-sidebar-text">
            Live schema mapping, worker orchestration, and validation — all on one control plane.
          </p>
        </div>

        {/* flow graph */}
        <div className="relative mx-auto w-full max-w-md">
          <svg viewBox="0 0 460 190" className="w-full">
            {EDGES.map(([a, b], i) => {
              const from = NODES[a]
              const to = NODES[b]
              return (
                <motion.line
                  key={i}
                  x1={from.x + 20}
                  y1={from.y + 12}
                  x2={to.x}
                  y2={to.y + 12}
                  stroke="#2563EB"
                  strokeWidth="1.5"
                  strokeDasharray="4 4"
                  initial={{ pathLength: 0, opacity: 0 }}
                  animate={{ pathLength: 1, opacity: 0.6 }}
                  transition={{ duration: 1.2, delay: 0.2 + i * 0.15, ease: 'easeOut' }}
                />
              )
            })}
            {NODES.map((n, i) => (
              <motion.g
                key={n.label}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.4, delay: 0.5 + i * 0.12 }}
              >
                <rect
                  x={n.x - 4}
                  y={n.y - 4}
                  width="128"
                  height="32"
                  rx="6"
                  fill="#1E293B"
                  stroke="#334155"
                />
                <circle cx={n.x + 8} cy={n.y + 12} r="3" fill="#22C55E">
                  <animate attributeName="opacity" values="1;0.3;1" dur="1.8s" repeatCount="indefinite" />
                </circle>
                <text x={n.x + 18} y={n.y + 10} fill="#F8FAFC" fontSize="9" fontFamily="JetBrains Mono, monospace">
                  {n.label}
                </text>
                <text x={n.x + 18} y={n.y + 21} fill="#94A3B8" fontSize="7.5">
                  {n.sub}
                </text>
              </motion.g>
            ))}
          </svg>
        </div>

        <div className="hidden sm:block" />
      </div>

      {/* floating live-metric chips */}
      {CHIPS.map((c) => (
        <motion.div
          key={c.label}
          className="absolute rounded border border-slate-700/60 bg-slate-800/70 px-3 py-2 backdrop-blur-sm"
          style={{ top: c.top, left: c.left }}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: [8, 0, 0, -4, 0] }}
          transition={{ duration: 4, delay: c.delay, repeat: Infinity, repeatType: 'loop', ease: 'easeInOut' }}
        >
          <p className="text-tiny text-sidebar-text">{c.label}</p>
          <p className="mono text-small font-semibold text-white">{c.value}</p>
        </motion.div>
      ))}
    </div>
  )
}
