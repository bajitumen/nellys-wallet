import { Component, type ReactNode } from "react";

type State = { error: Error | null };

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string }): void {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  reset = () => {
    this.setState({ error: null });
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--bg, #0f1011)",
            color: "var(--text, #fff)",
            padding: "2rem",
          }}
        >
          <div style={{ maxWidth: 480, textAlign: "center" }}>
            <h1 style={{ marginTop: 0 }}>Something went wrong.</h1>
            <p style={{ color: "var(--text-mid, #888)" }}>
              The page hit an unexpected error. Reload to try again.
            </p>
            <button
              type="button"
              onClick={this.reset}
              style={{
                marginTop: "1rem", padding: "0.5rem 1rem",
                background: "var(--accent, #4f46e5)", color: "#fff",
                border: 0, borderRadius: 6, cursor: "pointer",
              }}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
