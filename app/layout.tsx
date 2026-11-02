import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "YT Automation Studio",
  description: "Write, render, and ship YouTube videos from one focused workspace.",
  appleWebApp: { capable: true, title: "YT Studio", statusBarStyle: "black-translucent" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}