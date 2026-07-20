// Catch-all proxy: forwards /api/* to the backend VM, keeping its IP off the client.
// Edge runtime (not Node) so the SSE stream at /api/stream forwards without buffering.
export const config = { runtime: "edge" };

const VM_ORIGIN = process.env.VM_ORIGIN!;
const INTERNAL_KEY = process.env.INTERNAL_KEY!;

export default async function handler(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const target = `${VM_ORIGIN}${url.pathname}${url.search}`;

  const headers = new Headers(req.headers);
  headers.set("x-internal-key", INTERNAL_KEY);
  headers.set("host", new URL(VM_ORIGIN).host);

  const upstream = await fetch(target, {
    method: req.method,
    headers,
    body: ["GET", "HEAD"].includes(req.method) ? undefined : req.body,
    // @ts-expect-error required by edge runtime when forwarding a streaming body
    duplex: "half",
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
