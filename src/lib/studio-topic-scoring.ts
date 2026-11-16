/** Evidence-first topic scouting: interesting headlines are proposals, not facts. */
export type TopicOption = { angle: string; queries: string[]; clinical: boolean; hook?: string };
export type TopicSource = { title: string; text: string; url: string; year?: string; publisher?: string };

const STOPWORDS = new Set("the and from with what about when will they this that into have your been were for are you their video ai artificial intelligence how does research study studies new using based example examples".split(" "));
const tokens = (input: string) => new Set((input.toLowerCase().match(/[a-z][a-z0-9-]{2,}/g) || [])
  .filter((word) => !STOPWORDS.has(word)));
export function titleSimilarity(a: string, b: string): number {
  const left = tokens(a), right = tokens(b);
  if (!left.size || !right.size) return 0;
  const common = [...left].filter((word) => right.has(word)).length;
  return common / Math.max(1, Math.min(left.size, right.size));
}
export function primaryEvidence(source: TopicSource): boolean {
  return /^https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/\d+\/$/.test(source.url) ||
    /^https:\/\/eric\.ed\.gov\/\?id=E[JD]\d+$/.test(source.url);
}
export function evaluateTopic(option: TopicOption, sources: TopicSource[], recent: string[], year = new Date().getUTCFullYear()) {
  const unique = [...new Map(sources.map((item) => [item.url, item])).values()];
  const primary = unique.filter(primaryEvidence);
  const recentEvidence = primary.filter((source) => {
    const published = Number(source.year);
    return Number.isInteger(published) && published >= year - 4 && published <= year;
  }).length;
  const similarity = Math.max(0, ...recent.map((title) => titleSimilarity(option.angle, title)));
  const overlap = unique.reduce((total, source) => {
    const query = tokens(option.angle), sourceTitle = tokens(source.title);
    return total + [...query].filter((word) => sourceTitle.has(word)).length;
  }, 0);
  // Two records from one source family may still be separate studies: unique URL
  // and enough excerpt text are required, and Wikipedia never counts as primary.
  const wellFormed = primary.filter((source) => source.text.trim().length >= 100);
  const eligible = wellFormed.length >= 2 && similarity < .66 && unique.length >= 2;
  const score = primary.length * 16 + recentEvidence * 8 + Math.min(overlap, 8) * 2 +
    (option.hook?.length ? 2 : 0) - similarity * 55 - Math.max(0, unique.length - primary.length) * 2;
  return { eligible, score, primaryCount: primary.length, recentEvidence, similarity };
}
export function rankTopicOptions<T extends TopicOption, S extends TopicSource>(items: { option: T; sources: S[] }[],
                                                        recent: string[], year?: number) {
  return items.map((item) => ({ ...item, quality: evaluateTopic(item.option, item.sources, recent, year) }))
    .filter((item) => item.quality.eligible)
    .sort((a, b) => b.quality.score - a.quality.score);
}
