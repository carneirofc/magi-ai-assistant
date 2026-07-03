// Login screen. Posts the operator password to the login route, which sets the
// session cookie on success. A bad password redirects back here with ?error=1.

import { OutlineButton, StatusMessage, SurfacePanel, TextInput } from "@carneirofc/ui";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main className="grid min-h-screen place-items-center px-4">
      <SurfacePanel tone="soft" padding="lg" className="w-full max-w-sm">
        <form method="post" action="/api/auth/login" className="flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-[color:var(--ui-bg-active)] text-ui-md font-bold text-[color:var(--ui-ink-highlight)]">
              M
            </span>
            <div className="flex flex-col leading-tight">
              <strong className="cyber-title text-ui-lg">MAGI Admin</strong>
              <span className="text-ui-2xs uppercase tracking-[0.18em] text-[color:var(--ui-ink-subtle)]">
                Operator sign-in
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
