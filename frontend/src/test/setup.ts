import "@testing-library/jest-dom/vitest";

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
