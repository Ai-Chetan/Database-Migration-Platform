import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },

  server: {
    port: 5173,
  },

  build: {
    chunkSizeWarningLimit: 600,

    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return

          if (
            id.includes('react-router') ||
            id.includes('react-dom') ||
            /node_modules[\\/]+react[\\/]/.test(id)
          ) {
            return 'vendor-react'
          }

          if (
            id.includes('@tanstack/react-query') ||
            id.includes('@tanstack/react-table')
          ) {
            return 'vendor-query'
          }

          if (id.includes('recharts')) {
            return 'vendor-charts'
          }

          if (id.includes('framer-motion')) {
            return 'vendor-motion'
          }

          if (
            id.includes('react-hook-form') ||
            id.includes('@hookform/resolvers') ||
            id.includes('/zod/')
          ) {
            return 'vendor-forms'
          }
        },
      },
    },
  },
})