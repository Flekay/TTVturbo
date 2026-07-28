import { create } from "zustand";

/**
 * A single snapshot of scalar backend metrics, sampled at a point in time.
 * Used to drive the dashboard time-series charts. The status endpoint only
 * exposes the current values, so we accumulate samples client-side as the
 * query polls in (every few seconds).
 */
export interface StatusSample {
  ts: number;
  uptime_seconds: number;
  recordings_count: number;
  recordings_total_size_bytes: number;
  recordings_total_duration_seconds: number;
  storage_free_bytes: number;
}

export interface StatusHistoryState {
  samples: StatusSample[];
  push: (sample: StatusSample) => void;
  clear: () => void;
}

const MAX_SAMPLES = 60;

export const useStatusHistoryStore = create<StatusHistoryState>((set) => ({
  samples: [],
  push: (sample) =>
    set((state) => {
      const last = state.samples[state.samples.length - 1];
      // De-duplicate identical consecutive samples to keep charts clean.
      if (last && last.uptime_seconds === sample.uptime_seconds) {
        return state;
      }
      const next = [...state.samples, sample];
      if (next.length > MAX_SAMPLES) next.splice(0, next.length - MAX_SAMPLES);
      return { samples: next };
    }),
  clear: () => set({ samples: [] }),
}));
