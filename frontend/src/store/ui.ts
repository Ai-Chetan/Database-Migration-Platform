import { create } from 'zustand'

interface UIState {
  sidebarCollapsed: boolean
  toggleSidebar: () => void
  setSidebarCollapsed: (v: boolean) => void

  // The job the operator is currently "watching" in a live-monitoring context
  activeJobId: string | null
  setActiveJobId: (id: string | null) => void

  commandPaletteOpen: boolean
  setCommandPaletteOpen: (v: boolean) => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

  activeJobId: null,
  setActiveJobId: (id) => set({ activeJobId: id }),

  commandPaletteOpen: false,
  setCommandPaletteOpen: (v) => set({ commandPaletteOpen: v }),
}))
