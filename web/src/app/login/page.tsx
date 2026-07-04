// Login screen. The card lives in the library (LoginView); this server page
// resolves the ?error=1 flag from the redirect and renders it. Brand defaults to
// "MAGI Admin" — override the LoginView props to reskin.

import { LoginView } from "@carneirofc/magi-web/components/LoginView";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return <LoginView error={Boolean(error)} />;
}
