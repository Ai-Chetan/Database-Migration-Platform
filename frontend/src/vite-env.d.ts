/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_NAME: string
  readonly VITE_APP_TAGLINE: string
  readonly VITE_PRICING_STARTER: string
  readonly VITE_PRICING_PROFESSIONAL: string
  readonly VITE_PRICING_ENTERPRISE: string
  readonly VITE_PRICING_CURRENCY: string
  readonly VITE_PRICING_PERIOD: string
  readonly VITE_API_BASE_URL: string
  readonly VITE_SUPPORT_EMAIL: string
  readonly VITE_DOCS_URL: string
  readonly VITE_COMPANY_NAME: string
  readonly VITE_VERSION: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
