import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface UISettings {
  sidebarCollapsed: boolean;
  use24HourFormat: boolean;
  autoplayAfterRecord: boolean;
  confirmDelete: boolean;
}

interface UIState extends UISettings {
  topbarHidden: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
  setTopbarHidden: (hidden: boolean) => void;
  setUse24HourFormat: (value: boolean) => void;
  setAutoplayAfterRecord: (value: boolean) => void;
  setConfirmDelete: (value: boolean) => void;
  setSettings: (settings: Partial<UISettings>) => void;
}

const DEFAULT_SETTINGS: UISettings = {
  sidebarCollapsed: false,
  use24HourFormat: true,
  autoplayAfterRecord: false,
  confirmDelete: true,
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      ...DEFAULT_SETTINGS,
      topbarHidden: false,
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () =>
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setTopbarHidden: (hidden) => set({ topbarHidden: hidden }),
      setUse24HourFormat: (value) => set({ use24HourFormat: value }),
      setAutoplayAfterRecord: (value) => set({ autoplayAfterRecord: value }),
      setConfirmDelete: (value) => set({ confirmDelete: value }),
      setSettings: (settings) => set(settings),
    }),
    {
      name: "ttvturbo-ui",
      version: 1,
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        use24HourFormat: state.use24HourFormat,
        autoplayAfterRecord: state.autoplayAfterRecord,
        confirmDelete: state.confirmDelete,
      }),
    },
  ),
);
