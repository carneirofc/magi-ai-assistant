// Login screen. Posts the operator password to the login route, which sets the
// session cookie on success. A bad password redirects back here with ?error=1.

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main>
      <form className="card" method="post" action="/api/auth/login">
        <h1>MAGI Admin</h1>
        <label htmlFor="password" className="muted">
          Operator password
        </label>
        <input id="password" name="password" type="password" autoFocus required />
        {error ? <p className="error">Incorrect password.</p> : null}
        <button type="submit">Sign in</button>
      </form>
    </main>
  );
}
