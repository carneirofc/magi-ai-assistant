import type { Metadata } from "next";
import { ThemeToggleButton } from "@carneirofc/ui";
import "@carneirofc/ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAGI Admin",
  description: "Operator dashboard for MAGI memory & knowledge",
};

// Resolve + apply the theme before first paint so there's no light→dark flash.
// Mirrors @carneirofc/ui's ThemeToggleButton: it reads/writes the "ui-theme"
// localStorage key and toggles data-theme on <html>.
const THEME_INIT = `
try {
  var t = localStorage.getItem("ui-theme");
  if (t !== "light" && t !== "dark") {
    t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", t);
} catch (e) {
  document.documentElement.setAttribute("data-theme", "light");
}
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>
        {children}
        <ThemeToggleButton />
      </body>
    </html>
  );
}
