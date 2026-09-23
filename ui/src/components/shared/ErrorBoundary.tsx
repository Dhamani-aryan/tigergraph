import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertOctagon } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Short name of what failed, e.g. "Graph view". */
  label?: string;
  fallback?: ReactNode;
  resetKey?: unknown;
}
interface State {
  error?: Error;
  resetKey?: unknown;
}

/** Contains a rendering failure to one region; the rest of the page keeps working. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = {};

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) return { error: undefined, resetKey: props.resetKey };
    return null;
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface in dev tools without crashing the app.
    console.warn(`[${this.props.label ?? "view"}] render error`, error, info.componentStack);
  }

  override render() {
    if (!this.state.error) return this.props.children;
    if (this.props.fallback) return this.props.fallback;
    return (
      <div role="alert" className="flex items-start gap-3 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200">
        <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <div>
          <p className="font-semibold">{this.props.label ?? "This view"} could not be rendered.</p>
          <p className="mt-1 text-red-200/80">The rest of the page is unaffected. Details: {this.state.error.message}</p>
        </div>
      </div>
    );
  }
}
