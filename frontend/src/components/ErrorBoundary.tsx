import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("Unhandled UI error:", error, info);
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="error-boundary" role="alert">
          <h1 className="error-boundary__title">Die Oberfläche ist auf ein Problem gestoßen.</h1>
          <p className="error-boundary__message">
            {this.state.error?.message ?? "Unbekannter Fehler."}
          </p>
          <button className="btn btn--secondary" onClick={this.handleReset}>
            Oberfläche zurücksetzen
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
