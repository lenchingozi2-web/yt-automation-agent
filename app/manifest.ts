import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return { name: "YT Automation Studio", short_name: "YT Studio", description: "Create and queue YouTube automation scripts.", start_url: "/", display: "standalone", background_color: "#090b0f", theme_color: "#090b0f", icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }, { src: "/icon-512.png", sizes: "512x512", type: "image/png" }] };
}