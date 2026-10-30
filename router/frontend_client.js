/**
 * HCC-Fit Simple Router client (plain JS).
 * Never hardcode the API key in source.
 */
export async function searchHCC(req, options = {}) {
  const baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? 150000
  );
  const headers = { "Content-Type": "application/json" };
  if (options.apiKey) headers["X-SciencePal-API-Key"] = options.apiKey;
  if (options.routerToken) headers["Authorization"] = `Bearer ${options.routerToken}`;

  const body = {
    strategy: "auto",
    k: 5,
    deadline_ms: 100000,
    require_fulltext: true,
    ...req,
    request_id:
      req.request_id ??
      (globalThis.crypto?.randomUUID?.() ?? `req-${Date.now()}`),
  };

  try {
    const res = await fetch(`${baseUrl}/v1/search`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        `HCC-Fit search failed ${res.status}: ${JSON.stringify(data)}`
      );
    }
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

export function readablePapers(resp) {
  return resp.papers.filter((p) => p.readability?.level === "fulltext");
}
