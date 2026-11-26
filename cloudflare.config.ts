import { bindings, defineConfig, defineWorker } from "cf/config";

export default defineConfig({
  worker: defineWorker({
    name: "lenxiq-studio",
    entrypoint: "vinext/server/fetch-handler",
    compatibilityDate: "2026-11-26",
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
