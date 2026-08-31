
import { Component } from 'react';
import { Btn, Icon } from './primitives';

export class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('ClippyMe frontend crashed', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="fatal-shell" role="alert">
        <div className="fatal-card">
          <span className="fatal-icon"><Icon n="triangle-alert" /></span>
          <p className="eyebrow">Frontend recovery</p>
          <h1>ClippyMe hit an unexpected UI error.</h1>
          <p>Your rendered files and backend jobs are untouched. Reload the interface to recover.</p>
          <div className="fatal-actions">
            <Btn variant="grad" icon="refresh-cw" onClick={() => window.location.reload()}>Reload interface</Btn>
          </div>
          <details>
            <summary>Technical details</summary>
            <pre>{String(this.state.error?.message || this.state.error)}</pre>
          </details>
        </div>
      </main>
    );
  }
}
