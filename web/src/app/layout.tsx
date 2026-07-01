import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAGI Admin",
  description: "Operator admin for MAGI memory & knowledge",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
