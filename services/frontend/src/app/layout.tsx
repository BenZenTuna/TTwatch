import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TTwatch — Intelligence Dashboard",
  description: "Real-time intelligence monitoring and analysis platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
