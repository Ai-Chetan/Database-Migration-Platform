import React from 'react'
import { Link } from 'react-router-dom'
import {
  Database,
  Zap,
  ShieldCheck,
  GitCompare,
  Gauge,
  Workflow,
  Check,
  ArrowRight,
} from 'lucide-react'
import { Button } from '@/components/common'

const APP_NAME = import.meta.env.VITE_APP_NAME || 'Migration Platform'
const TAGLINE =
  import.meta.env.VITE_APP_TAGLINE || 'Enterprise Database Migration, Without the Enterprise Tax'
const CURRENCY = import.meta.env.VITE_PRICING_CURRENCY || 'USD'
const PERIOD = import.meta.env.VITE_PRICING_PERIOD || 'month'
const CURRENCY_SYMBOL = CURRENCY === 'USD' ? '$' : CURRENCY + ' '

const FEATURES = [
  {
    icon: GitCompare,
    title: 'Intelligent Schema Mapping',
    description:
      'Automatic type conversion, constraint mapping, and column-level recommendations across MySQL, PostgreSQL, and more.',
  },
  {
    icon: Workflow,
    title: 'Typed Workflow Engine',
    description:
      'An 8-node execution pipeline — read, transform, validate, write, audit — orchestrated with full observability.',
  },
  {
    icon: Gauge,
    title: 'Worker Sweep Simulation',
    description:
      "Simulate worker counts before you run anything and find the sweet spot for duration, cost, and failure risk.",
  },
  {
    icon: ShieldCheck,
    title: 'Data Masking & Synthetic Data',
    description:
      'Hash, redact, or synthesize sensitive columns automatically with AI-suggested masking rules.',
  },
  {
    icon: Zap,
    title: 'Live Operations Console',
    description:
      'Pause, resume, retry, or quarantine workers and chunks in real time while a migration is running.',
  },
  {
    icon: Database,
    title: 'Extended Connectors',
    description:
      'Relational databases, object storage, Kafka streams, and REST APIs — one platform, every source.',
  },
]

const TIERS = [
  {
    name: 'Starter',
    price: import.meta.env.VITE_PRICING_STARTER || '299',
    description: 'For small teams running occasional migrations.',
    features: ['Up to 3 concurrent jobs', '2 connectors included', 'Email support', 'Standard reporting'],
  },
  {
    name: 'Professional',
    price: import.meta.env.VITE_PRICING_PROFESSIONAL || '899',
    description: 'For teams migrating regularly at scale.',
    features: [
      'Unlimited concurrent jobs',
      'All connectors included',
      'Priority support',
      'Data masking & synthetic data',
      'Scheduler & automation',
    ],
    highlighted: true,
  },
  {
    name: 'Enterprise',
    price: import.meta.env.VITE_PRICING_ENTERPRISE || '2499',
    description: 'For organizations with compliance & scale needs.',
    features: [
      'Everything in Professional',
      'RBAC & audit log',
      'Dedicated support engineer',
      'Custom SLAs',
      'On-prem / VPC deployment',
    ],
  },
]

export default function Landing() {
  return (
    <div className="flex min-h-screen flex-col">
      {/* Nav */}
      <header className="sticky top-0 z-30 border-b border-border bg-white/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded bg-action">
              <Database className="h-4 w-4 text-white" />
            </div>
            <span className="text-body font-semibold text-text-primary">{APP_NAME}</span>
          </div>
          <nav className="hidden items-center gap-8 md:flex">
            <a href="#features" className="text-small text-text-secondary hover:text-text-primary">
              Features
            </a>
            <a href="#pricing" className="text-small text-text-secondary hover:text-text-primary">
              Pricing
            </a>
          </nav>
          <div className="flex items-center gap-3">
            <Link to="/login">
              <Button variant="ghost" size="sm">
                Log in
              </Button>
            </Link>
            <Link to="/login">
              <Button size="sm">Get started</Button>
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-4xl px-6 pb-20 pt-24 text-center">
        <h1 className="text-h1 text-text-primary md:text-5xl">{TAGLINE}</h1>
        <p className="mx-auto mt-5 max-w-2xl text-body text-text-secondary md:text-lg">
          Assess, simulate, mask, migrate, and validate database migrations from a single control plane —
          built for teams who need enterprise-grade reliability without the enterprise price tag.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link to="/login">
            <Button size="lg" rightIcon={<ArrowRight className="h-4 w-4" />}>
              Start migrating
            </Button>
          </Link>
          <a href="#features">
            <Button size="lg" variant="secondary">
              See how it works
            </Button>
          </a>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="border-t border-border bg-surface py-20">
        <div className="mx-auto max-w-6xl px-6">
          <h2 className="text-center text-h2 text-text-primary">Everything a migration team needs</h2>
          <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded border border-border bg-white p-6 shadow-sm">
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded bg-action/10">
                  <f.icon className="h-5 w-5 text-action" />
                </div>
                <h3 className="text-h4 text-text-primary">{f.title}</h3>
                <p className="mt-2 text-body text-text-secondary">{f.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20">
        <div className="mx-auto max-w-6xl px-6">
          <h2 className="text-center text-h2 text-text-primary">Simple, transparent pricing</h2>
          <p className="mx-auto mt-3 max-w-xl text-center text-body text-text-secondary">
            Every plan includes core migration, schema mapping, and monitoring capabilities.
          </p>
          <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
            {TIERS.map((tier) => (
              <div
                key={tier.name}
                className={
                  'flex flex-col rounded border p-8 ' +
                  (tier.highlighted
                    ? 'border-action shadow-sm ring-1 ring-action'
                    : 'border-border shadow-sm')
                }
              >
                {tier.highlighted && (
                  <span className="mb-3 inline-block w-fit rounded-pill bg-action/10 px-2 py-0.5 text-tiny font-medium text-action">
                    Most popular
                  </span>
                )}
                <h3 className="text-h4 text-text-primary">{tier.name}</h3>
                <p className="mt-1 text-small text-text-secondary">{tier.description}</p>
                <p className="mt-6 text-h1 text-text-primary">
                  {CURRENCY_SYMBOL}
                  {tier.price}
                  <span className="text-body font-normal text-text-secondary">/{PERIOD}</span>
                </p>
                <ul className="mt-6 flex-1 space-y-3">
                  {tier.features.map((f) => (
                    <li key={f} className="flex items-start gap-2 text-small text-text-secondary">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                      {f}
                    </li>
                  ))}
                </ul>
                <Link to="/login" className="mt-8">
                  <Button className="w-full" variant={tier.highlighted ? 'primary' : 'secondary'}>
                    Choose {tier.name}
                  </Button>
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="mt-auto border-t border-border py-10">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 text-small text-text-tertiary md:flex-row">
          <p>
            © {new Date().getFullYear()} {import.meta.env.VITE_COMPANY_NAME || APP_NAME}. All rights reserved.
          </p>
          <div className="flex gap-6">
            <a href={`mailto:${import.meta.env.VITE_SUPPORT_EMAIL}`} className="hover:text-text-primary">
              Support
            </a>
            <a href={import.meta.env.VITE_DOCS_URL} className="hover:text-text-primary" target="_blank" rel="noreferrer">
              Docs
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}
