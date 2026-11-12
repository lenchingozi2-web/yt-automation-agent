import { readFileSync } from "node:fs";
import { researchedDraft } from "./studio-research.ts";

async function main() {
  const { brief, seconds, ratio, recent } = JSON.parse(readFileSync(0, "utf-8"));
  process.stdout.write(JSON.stringify(await researchedDraft(brief, seconds, ratio, recent)));
}

void main().catch((error: Error) => {
  process.stderr.write(error.message + "\n");
  process.exitCode = 1;
});
