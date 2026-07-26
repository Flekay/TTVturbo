import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "../components/ui/ToastProvider";

interface RenderOptions {
  initialEntries?: string[];
}

export function renderWithProviders(ui: ReactElement, options: RenderOptions = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0, refetchInterval: false },
      mutations: { retry: false },
    },
  });

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={options.initialEntries ?? ["/"]}>
          <ToastProvider>{children}</ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );
  }

  return { ...render(ui, { wrapper: Wrapper }), queryClient };
}

/** Install a fetch mock and return helpers to control its responses. */
export function installFetchMock() {
  const calls: { url: string; method: string; body?: FormData | unknown }[] = [];
  const responses = new Map<string, { status: number; body: unknown }>();

  const setResponse = (url: string, status: number, body: unknown) => {
    responses.set(url, { status, body });
  };

  const original = globalThis.fetch;

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body });
    const key = `${method} ${url}`;
    const exact = responses.get(key) ?? responses.get(url);
    if (!exact) {
      return new Response(JSON.stringify({ detail: "Not found" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      });
    }
    const bodyStr =
      typeof exact.body === "string" ? exact.body : JSON.stringify(exact.body);
    return new Response(bodyStr, {
      status: exact.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  return {
    calls,
    setResponse,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}
