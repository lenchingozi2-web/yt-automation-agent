// The database returns a bounded, id-sorted manifest with an exact count.
// An incomplete manifest must never serve an "unchanged" response.
export async function queueFingerprint(
  rows: { id: string; updated_at: string | null }[] | null,
  count: number | null,
  day: string,
): Promise<string | null> {
  if (!rows || count === null || count !== rows.length || rows.some(row => !row.id || !row.updated_at)) return null;
  const bytes = new TextEncoder().encode(JSON.stringify([day, rows]));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}
