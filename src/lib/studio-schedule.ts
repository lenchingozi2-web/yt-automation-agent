type ResearchSchedule = { url: string; key: string; token: string };
type BriefingRow = { briefing_date: string; topic_area: string; references: string[] };
export const lagosDay = (now: Date) => new Date(now.getTime() + 3600_000).toISOString().slice(0, 10);
export function freshBriefing(rows: BriefingRow[], today: string) {
  const beats = new Set(rows.filter(row => row.briefing_date === today && new Set(row.references ?? []).size >= 2).map(row => row.topic_area));
  return ["health", "education", "intelligence"].every(beat => beats.has(beat));
}

/** Two tiny daily freshness reads. Heavy research stays on the existing Actions worker. */
export async function scheduledResearch(config: ResearchSchedule, now = new Date(), request: typeof fetch = fetch) {
  if (!config.url || !config.key || !config.token) throw new Error("Daily research schedule is missing its configuration.");
  const headers: Record<string, string> = { apikey: config.key };
  if (!config.key.startsWith("sb_secret_")) headers.Authorization = "Bearer " + config.key;
  const response = await request(config.url.replace(/\/$/, "") + "/rest/v1/research_ideas?select=briefing_date,topic_area,references&order=briefing_date.desc&limit=3", {
    headers, signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("Daily research freshness check failed: HTTP " + response.status);
  if (freshBriefing(await response.json() as BriefingRow[], lagosDay(now))) return { dispatched: false, fresh: true };
  const dispatched = await request("https://api.github.com/repos/lenchingozi2-web/yt-automation-agent/actions/workflows/nightly_research.yml/dispatches", {
    method: "POST", headers: { Authorization: "Bearer " + config.token, "Content-Type": "application/json",
      Accept: "application/vnd.github+json", "User-Agent": "LenxiQ-Studio", "X-GitHub-Api-Version": "2022-11-28" },
    body: JSON.stringify({ ref: "main", inputs: { preview_only: "false" } }), signal: AbortSignal.timeout(10_000),
  });
  if (!dispatched.ok) throw new Error("Daily research dispatch failed: HTTP " + dispatched.status);
  console.log(JSON.stringify({ event: "daily-research-dispatched", day: lagosDay(now) }));
  return { dispatched: true, fresh: false };
}
