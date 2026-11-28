import { titleSimilarity } from "./studio-topic-scoring.ts";

type Packaging = { title: string; thumbnail_title: string };
const generic = /^(?:exploring|understanding|a deep dive|the future of|unlocking)\b|\b(?:mind.?blowing|you won.t believe|changes everything|miracle cure|shocking truth)\b/i;
const terms = (text: string) => new Set((text.toLowerCase().match(/[a-z][a-z-]{2,}/g) ?? [])
  .filter(word => !/^(the|and|for|with|how|why|what|this|that|your|can|does|are)$/.test(word)));

/** Rank three editorial proposals without extra model requests. Evidence is audited afterward. */
export function selectPackaging(value: unknown, subject: string, recent: string[]): Packaging | undefined {
  if (!Array.isArray(value)) return;
  const subjectTerms = terms(subject);
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const title = typeof item.title === "string" ? item.title.replace(/\s+/g, " ").trim() : "";
    const headline = typeof item.thumbnail_title === "string" ? item.thumbnail_title.replace(/\s+/g, " ").trim() : "";
    const length = headline.split(" ").length;
    if (title.length < 20 || title.length > 95 || generic.test(title) || generic.test(headline) ||
        length < 2 || length > 5 || headline.length > 40 || /[<>]|https?:/i.test(title + headline) ||
        title.toLowerCase().includes(headline.toLowerCase()) || recent.some(old => titleSimilarity(old, title) >= .8)) return [];
    const overlap = [...terms(title)].filter(word => subjectTerms.has(word)).length;
    if (!overlap) return [];
    const redundant = [...terms(headline)].filter(word => terms(title).has(word)).length;
    const score = Math.min(overlap, 4) * 3 + (title.length >= 45 && title.length <= 75 ? 4 : 0) - redundant * 2;
    return [{ title, thumbnail_title: headline, score }];
  }).sort((a, b) => b.score - a.score)[0];
}
