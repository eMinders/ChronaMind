import { vi } from "vitest";

/** One request the stub received, with its body already decoded. */
export interface StubCall {
  method: string;
  path: string;
  headers: Record<string, string>;
  json?: unknown;
  form?: Record<string, string>;
}

type Answer = { status: number; body: unknown } | (() => never);
type Routes = Record<string, Answer | ((call: StubCall) => Answer)>;

export function json(status: number, body: unknown): Answer {
  return { status, body };
}

/**
 * Replaces `fetch` with a scripted server keyed by `"METHOD /path"` (the query
 * string is ignored). Unknown routes answer 404 so a missing script is loud.
 */
export function stubApi(routes: Routes) {
  const calls: StubCall[] = [];
  const fetchMock = vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    const call: StubCall = { method, path, headers };
    if (typeof init.body === "string") {
      call.json = JSON.parse(init.body);
    } else if (init.body instanceof URLSearchParams) {
      call.form = Object.fromEntries(init.body.entries());
    }
    calls.push(call);
    const route = routes[`${method} ${path.split("?")[0]}`];
    const answer = typeof route === "function" ? route(call) : route;
    if (answer === undefined) {
      return new Response(JSON.stringify({ detail: "not scripted" }), {
        status: 404,
      });
    }
    if (typeof answer === "function") {
      return answer();
    }
    return new Response(JSON.stringify(answer.body), {
      status: answer.status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
