import { Component, type ReactNode } from "react";
import { reportClientError } from "../services/diagnostics";

export class AppErrorBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = {failed: false};
  static getDerivedStateFromError() { return {failed: true}; }
  componentDidCatch(error: Error) { reportClientError("react", error); }
  render() {
    if (this.state.failed) return <main role="alert" style={{padding: 32}}>
      <h1>Something went wrong</h1>
      <p>Your submitted jobs continue in the background. Reload and open your Library to find them.</p>
      <button onClick={() => globalThis.location.reload()}>Reload recastfx</button>
    </main>;
    return this.props.children;
  }
}
