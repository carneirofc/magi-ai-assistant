import type { Metadata } from "next";
import { ThemeToggleButton } from "@carneirofc/ui";
import { themeInitScript } from "@carneirofc/magi-web/lib/theme";
import "@carneirofc/ui/styles.css";
import "./globals.css";

// Branding + document metadata are policy — they stay here in the app, not the
// library. The theme-flash-prevention script is mechanism, imported from the
// library (see @carneirofc/magi-web/lib/theme).
export const metadata: Metadata = {
  title: "MAGI Admin",
  description: "Operator dashboard for MAGI memory & knowledge",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        {children}
        <ThemeToggleButton />
      </body>
    </html>
  );
}
