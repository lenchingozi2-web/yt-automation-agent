import { NextResponse } from "next/server";
import { getAdminClient } from "@/src/lib/supabase-admin";

export async function POST(request: Request) {
  try {
    const { id } = await request.json();
    if (typeof id !== "string") return NextResponse.json({ error: "A video id is required." }, { status: 400 });
    const { data, error } = await getAdminClient().from("videos").update({ status: "rendering" }).eq("id", id).eq("status", "pending").select().single();
    if (error) return NextResponse.json({ error: "Only pending videos can be rendered." }, { status: 409 });
    return NextResponse.json(data);
  } catch (error) {
    console.error("Render queue update failed", error);
    return NextResponse.json({ error: "Could not start rendering." }, { status: 500 });
  }
}