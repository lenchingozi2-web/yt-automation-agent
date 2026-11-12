import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LenxiQ Production Studio",
  description: "Create, review, render, and upload YouTube videos from your LenxiQ workspace.",
  icons: { icon: "/icon-192.png", apple: "/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "LenxiQ Studio", statusBarStyle: "black-translucent" },
};
export const viewport: Viewport = { themeColor: "#0d1211", colorScheme: "dark" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
