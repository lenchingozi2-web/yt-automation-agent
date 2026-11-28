# LenxiQ Studio – isolated Cloudflare migration

The original Next.js/Vercel build commands and Python/GitHub Actions pipeline are preserved. This branch introduces the official vinext Cloudflare adapter. There are no R2, KV, paid image transformations, database changes, or new rendering workers. No production Vercel deployment or DNS is removed by this branch.

## Validation and deployment

1. Verify the `Studio Cloudflare preview checks` workflow passes for this exact branch revision. It tests the Worker build and unauthenticated pages/routes against a disabled-production placeholder setup. No production secrets are required for that CI run.
2. Connect only `lenchingozi2-web/yt-automation-agent` in Cloudflare Workers Builds. Use the `chore/cloudflare-studio-migration` branch as the temporary deployment source and `lenxiq-studio` as the Worker name. Do not enable a paid plan.
3. Use Node 24, install command `npm ci`, leave the build command empty (the deploy command builds), deploy command `npm run deploy:vinext`, preview command `npx vinext-cloudflare previews deploy`. Keep the existing Vercel site as fallback. Depending on Workers Builds UI/version, use the Cloudflare vinext application workflow rather than Wrangler/OpenNext autoconfiguration.
4. Configure `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` as build variables **and runtime secrets** (runtime secrets persist across deploys). Configure `SUPABASE_SERVICE_ROLE_KEY`, `STUDIO_ALLOWED_EMAILS`, `STUDIO_GITHUB_DISPATCH_TOKEN`, and the existing script provider key(s) as server-side secrets only. Set `SUPABASE_VIDEO_BUCKET`, `SUPABASE_THUMBNAIL_BUCKET`, and other existing Studio settings as runtime secrets to the same configured values as Vercel, if used. Deploy the Worker before adding its runtime secrets, then redeploy after setting build-time variables. Never put production values in Git, chat, or build logs.
5. Test the temporary `workers.dev` URL using the real authorized Studio sign-in, briefing, script generation/import/revision, queue listing, and a disposable render. Confirm the scheduled GitHub worker still dispatches and the existing YouTube approval gate works. Observe Cloudflare free plan CPU, memory and subrequest limits; move costly work to an existing backend or keep Studio on Vercel if these tests fail.
6. Preserve the existing privacy/terms hosting until the YouTube OAuth and application links have been audited and migrated. Do not change DNS or disable Vercel until the full Cloudflare flow passes.

The Worker has a 10 ms CPU budget per request on the Free plan. A successful build is not evidence that long script-generation or revision routes are suitable for that budget. Cloudflare is a candidate, not a certified replacement until live testing.
