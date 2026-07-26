import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppRouter } from "./router";
import { ToastProvider } from "./components/ui/ToastProvider";
import { ErrorBoundary } from "./components/ErrorBoundary";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ToastProvider>
            <AppRouter />
          </ToastProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
