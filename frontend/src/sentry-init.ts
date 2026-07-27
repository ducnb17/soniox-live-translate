/**
 * Sentry browser-side initialisation.
 *
 * Import this module **once**, at the very top of `app.ts`, before any other
 * imports that might throw.  The init is a no-op when `VITE_SENTRY_DSN` is
 * absent or empty, so it is completely safe to leave in production builds
 * that don't need Sentry.
 *
 * Environment variables (set in `.env` or CI):
 *
 *   VITE_SENTRY_DSN         = https://<key>@o<org>.ingest.sentry.io/<project>
 *   VITE_SENTRY_ENVIRONMENT = production | staging | development  (default: development)
 *   VITE_APP_VERSION        = 0.4.1  (injected by Vite from package.json if needed)
 *
 * Sentry is tree-shaken in production if `VITE_SENTRY_DSN` is undefined at
 * build time (Vite replaces `import.meta.env.*` with literals and dead code
 * is eliminated by Rollup).
 */

const dsn: string = (import.meta.env.VITE_SENTRY_DSN as string | undefined) ?? "";

export function initSentry(): void {
  if (!dsn) return;

  // Dynamic import so the @sentry/browser bundle is only loaded when a DSN
  // is actually configured — keeps the JS bundle smaller in DSN-less deploys.
  import("@sentry/browser").then(
    ({ init, browserTracingIntegration, replayIntegration }) => {
      const environment: string =
        (import.meta.env.VITE_SENTRY_ENVIRONMENT as string | undefined) ??
        (import.meta.env.DEV ? "development" : "production");

      const release: string =
        (import.meta.env.VITE_APP_VERSION as string | undefined) ?? undefined!;

      init({
        dsn,
        environment,
        release,

        integrations: [
          // Adds automatic performance tracing for navigation and page loads.
          browserTracingIntegration(),
          // Session Replay — records a video-like replay for errors.
          // Only 10 % of sessions, 100 % of sessions with errors.
          replayIntegration({
            maskAllText: false,    // keep transcript text visible in replays
            blockAllMedia: false,
          }),
        ],

        // Performance: capture 10 % of navigation transactions.
        tracesSampleRate: 0.1,
        // Replay: 10 % normal sessions, 100 % on error.
        replaysSessionSampleRate: 0.1,
        replaysOnErrorSampleRate: 1.0,

        // Privacy: do not send user IP or personal data automatically.
        sendDefaultPii: false,

        // Filter out noise from routine WebSocket reconnects / audio glitches.
        beforeSend(event) {
          const msg = event.message ?? "";
          const tag = event.tags?.["type"] as string | undefined;
          if (
            tag === "ws_reconnect" ||
            msg.includes("NetworkError") ||
            msg.includes("WebSocket is closed")
          ) {
            return null;   // drop — not actionable
          }
          return event;
        },
      });

      console.debug("[sentry] initialised", { environment, release });
    },
    (err) => {
      // @sentry/browser not installed — silently skip.
      console.debug("[sentry] @sentry/browser not available:", err);
    },
  );
}
