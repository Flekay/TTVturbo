import "@testing-library/jest-dom/vitest";

// jsdom may not provide localStorage in some node runners; provide an
// in-memory stub so zustand/persist works in tests.
if (typeof globalThis !== "undefined" && !(globalThis as { localStorage?: Storage }).localStorage) {
  const store = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return store.size;
    },
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => {
      store.clear();
    },
  };
  (globalThis as { localStorage?: Storage }).localStorage = stub;
  if (typeof window !== "undefined") {
    (window as { localStorage?: Storage }).localStorage = stub;
  }
}

// jsdom does not implement matchMedia or AudioContext; provide minimal stubs.
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    });
  }
  if (!(window as { AudioContext?: unknown }).AudioContext) {
    (window as { AudioContext?: unknown }).AudioContext = class {
      createMediaStreamSource() {
        return { connect: () => undefined };
      }
      createAnalyser() {
        return {
          fftSize: 0,
          frequencyBinCount: 0,
          connect: () => undefined,
          disconnect: () => undefined,
          getByteTimeDomainData: () => undefined,
        };
      }
      close() {
        return Promise.resolve();
      }
    };
  }
}
