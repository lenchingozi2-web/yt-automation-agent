import { bindings, defineConfig, defineWorker, triggers } from "cf/config";

export default defineConfig({
  worker: defineWorker({
    name: "lenxiq-studio",
    entrypoint: "./cloudflare-entry.js",
    compatibilityDate: "2026-11-26",
    triggers: [triggers.scheduled({ schedule: "11 23 * * *" }), triggers.scheduled({ schedule: "19 1 * * *" })],
    observability: { enabled: true, headSamplingRate: 0.1, traces: { enabled: true, headSamplingRate: 0.1 } },
    assets: {
      htmlHandling: "none",
      notFoundHandling: "none",
      runWorkerFirst: ["/*", "!/_next/static/*", "!/*/_next/static/*", "!/icon.svg", "!/icon-192.png", "!/icon-512.png", "!/apple-touch-icon.png", "!/lenxiq.jpg", "!/sw.js", "!/manifest.webmanifest"],
    },
    env: {
      ASSETS: bindings.assets(),
    },
  }),
});
