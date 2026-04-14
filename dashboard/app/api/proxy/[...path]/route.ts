import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.API_URL || "https://formly-api-pnem.onrender.com";

export const maxDuration = 120; // Allow long-running agent calls

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}

async function proxy(req: NextRequest, pathSegments: string[]) {
  const apiPath = "/api/" + pathSegments.join("/");
  const url = `${BACKEND}${apiPath}`;

  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;

  try {
    // Use arrayBuffer for binary-safe forwarding (handles multipart/form-data)
    const body = req.method !== "GET" ? await req.arrayBuffer() : undefined;

    const res = await fetch(url, {
      method: req.method,
      headers,
      body: body ? Buffer.from(body) : undefined,
    });

    const data = await res.arrayBuffer();

    return new NextResponse(Buffer.from(data), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("Content-Type") || "application/json" },
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: "Backend unavailable", detail: err.message },
      { status: 502 }
    );
  }
}
