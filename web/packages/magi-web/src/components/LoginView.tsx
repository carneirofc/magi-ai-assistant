// Operator sign-in card. Posts the password to the login route (default
// /api/auth/login), which sets the session cookie on success; a bad password
// redirects back with ?error=1. Brand + action are overridable so an overlay can
// reskin it without forking. Presentational — the consuming page resolves the
// `error` flag from its search params.

import { OutlineButton, StatusMessage, SurfacePanel, TextInput } from "@carneirofc/ui";

export function LoginView({
  error = false,
  brand = "MAGI Admin",
  tagline = "Operator sign-in",
  logo = "M",
  action = "/api/auth/login",
}: {
  error?: boolean;
  brand?: string;
  tagline?: string;
  logo?: string;
  action?: string;
}) {
  return (
    <main className="grid min-h-screen place-items-center px-4">
      <SurfacePanel tone="soft" padding="lg" className="w-full max-w-sm">
        <form method="post" action={action} className="flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-[color:var(--ui-bg-active)] text-ui-md font-bold text-[color:var(--ui-ink-highlight)]">
              {logo}
            </span>
            <div className="flex flex-col leading-tight">
              <strong className="cyber-title text-ui-lg">{brand}</strong>
              <span className="text-ui-2xs uppercase tracking-[0.18em] text-[color:var(--ui-ink-subtle)]">
                {tagline}
              </span>
            </div>
          </div>

          <label className="flex flex-col gap-1">
            <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">
              Operator password
            </span>
            <TextInput id="password" name="password" type="password" autoFocus required />
          </label>

          {error ? (
            <StatusMessage role="alert" tone="error">
              Incorrect password.
            </StatusMessage>
          ) : null}

          <OutlineButton type="submit" variant="accent" controlSize="lg">
            Sign in
          </OutlineButton>
        </form>
      </SurfacePanel>
    </main>
  );
}

export default LoginView;
