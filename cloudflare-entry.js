import app from "vinext/server/fetch-handler";
import { scheduledResearch } from "./src/lib/studio-schedule.ts";
export * from "vinext/server/fetch-handler";

const studio = {
  ...app,
  async scheduled(controller, env) {
    return scheduledResearch({
      url: env.NEXT_PUBLIC_SUPABASE_URL,
      key: env.SUPABASE_SERVICE_ROLE_KEY,
      token: env.STUDIO_GITHUB_DISPATCH_TOKEN,
    }, new Date(controller.scheduledTime));
  },
};
export default studio;
