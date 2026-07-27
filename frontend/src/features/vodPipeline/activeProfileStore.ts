import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Active Twitch profile selector.
 *
 * The selected profile id is the only piece of VOD-pipeline UI state that
 * needs to survive a page reload, so it lives in localStorage via
 * zustand/persist. All other state (lists, filters, dialogs) is server
 * state via TanStack Query or ephemeral useState.
 *
 * The store validates the persisted id lazily: if the id no longer
 * matches a real profile, the consumer is expected to clear it. We do
 * not fetch profiles here to keep this store independent of React Query.
 */

interface ActiveProfileState {
  activeProfileId: string | null;
  setActiveProfile: (id: string | null) => void;
  clearActiveProfile: () => void;
}

export const useActiveProfileStore = create<ActiveProfileState>()(
  persist(
    (set) => ({
      activeProfileId: null,
      setActiveProfile: (id) => set({ activeProfileId: id }),
      clearActiveProfile: () => set({ activeProfileId: null }),
    }),
    {
      name: "ttvturbo.vod-pipeline.active-profile",
      version: 1,
    },
  ),
);
