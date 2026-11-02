import { NextResponse } from "next/server";
import { getRequestClient } from "@/src/lib/supabase-admin";

async function dispatchWorker() {
  const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN;
  if (!token) return false;
  const repository = process.env.GITHUB_REPOSITORY || "lenchingozi2-web/yt-automation-agent";
  const response = await fetch(`https://api.github.com/repos/${repository}/actions/workflows/studio_worker.yml/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
    cache: "no-store",
  });
  if (!response.ok) {
    console.warn(`GitHub worker dispatch failed (${response.status}): ${await response.text()}`);
    return false;
  }
  return true;
}

export async function POST(request: Request) {
  try {
    const { id } = await request.json();
    if (typeof id !== "string") return NextResponse.json({ error: "A video id is required." }, { status: 400 });
    const { data, error } = await getRequestClient(request).from("videos").update({ status: "rendering" }).eq("id", id).eq("status", "pending").select().single();
    if (error) return NextResponse.json({ error: "Only pending videos can be rendered." }, { status: 409 });
    const workerDispatched = await dispatchWorker();
    return NextResponse.json({ ...data, workerDispatched });
  } catch (error) {
    console.error("Render queue update failed", error);
    return NextResponse.json({ error: "Could not start rendering." }, { status: 500 });
  }
}
