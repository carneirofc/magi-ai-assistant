// Layout for every authed dashboard page: the app owns shell/nav assembly explicitly
// and uses the library's thin shell helper as a convenience, not a hidden app builder.

import {
  buildDefaultAppShellConfig,
  MagiAppShell,
} from "@carneirofc/magi-web/slices/shell";

const shell = buildDefaultAppShellConfig();

export default function AppGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <MagiAppShell
      nav={shell.nav}
      brand={shell.brand}
      tagline={shell.tagline}
      logo={shell.logo}
    >
      {children}
    </MagiAppShell>
  );
}
