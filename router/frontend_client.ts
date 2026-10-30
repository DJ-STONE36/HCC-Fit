/**
 * HCC-Fit Simple Router client.
 * - Never hardcode the API key in source.
 * - Prefer a backend proxy with SCIENCEPAL_API_KEY; header mode is for controlled frontends.
 */

export type Strategy = "fast" | "balanced" | "auto";

export interface Scenario {
  text: string;
  disease?: string;
  treatment_state?: string;
  time_zero?: string;
  outcome?: string;
  horizon_months?: number;
  variables?: string[];
}

export interface SearchRequest {
  scenario: Scenario;
  strategy?: Strategy;
  k?: number;
  deadline_ms?: number;
  thinking?: boolean;
  require_fulltext?: boolean;
  request_id?: string;
}

export interface Readability {
  level: "fulltext" | "abstract_only" | "metadata_only";
  pmcid: string;
  url: string;
  full_text: boolean;
}

export interface Paper {
  pmid?: string;
  doi?: string;
  title?: string;
  journal?: string;
  year?: number;
  why?: string[];
  risk?: string[];
  sp_fit?: number;
  sp_reason?: string;
  readability?: Readability;
}

export interface SearchResponse {
  request_id: string;
  strategy_requested: string;
  strategy_used: "fast" | "balanced";
  scenario: Scenario;
  papers: Paper[];
  meta: Record<string, unknown>;
  warnings: string[];
  next_actions: string[];
}

export interface ClientOptions {
  baseUrl?: string;
  /** Optional. If omitted, backend must have SCIENCEPAL_API_KEY. */
  apiKey?: string;
  /** Optional service auth token, if HCCFIT_ROUTER_TOKEN is configured. */
  routerToken?: string;
  timeoutMs?: number;
}

export async function searchHCC(
  req: SearchRequest,
  options: ClientOptions = {}
): Promise<SearchResponse> {
  const baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? 150_000
  );
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (options.apiKey) {
    headers["X-SciencePal-API-Key"] = options.apiKey;
  }
  if (options.routerToken) {
    headers["Authorization"] = `Bearer ${options.routerToken}`;
  }
  const body: SearchRequest = {
    strategy: "auto",
    k: 5,
    deadline_ms: 100_000,
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
    return data as SearchResponse;
  } finally {
    clearTimeout(timeout);
  }
}

/** Optional: only show fulltext items in the "精读" tab. */
export function readablePapers(resp: SearchResponse): Paper[] {
  return resp.papers.filter((p) => p.readability?.level === "fulltext");
}
