// Auth is `credentials: "same-origin"` only — backend reads Clerk's __session
// cookie. Don't add an Authorization header without updating auth.py too.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(typeof body === "object" && body && "error" in body
      ? String((body as { error: unknown }).error)
      : `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

let _csrfToken: string | null = null;

async function fetchCsrfToken(force = false): Promise<string> {
  if (_csrfToken && !force) return _csrfToken;
  const res = await fetch("/api/csrf-token", { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  const data = (await res.json()) as { token: string };
  _csrfToken = data.token;
  return data.token;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  if (!res.ok) {
    const body = text ? safeParse(text) : null;
    throw new ApiError(res.status, body ?? text);
  }
  if (!text) return null as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    // 2xx with non-JSON body means an upstream proxy intercepted us.
    throw new ApiError(res.status, "Server returned non-JSON response");
  }
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  return jsonOrThrow<T>(res);
}

async function sendWithCsrf(url: string, method: "POST" | "DELETE", body?: unknown): Promise<Response> {
  const send = async (token: string) => fetch(url, {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      "X-CSRFToken": token,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const first = await send(await fetchCsrfToken());
  // flask-wtf returns 400, not 403 — sniff body for CSRF, retry once.
  if (first.status !== 400 && first.status !== 403) return first;
  const peek = await first.clone().text();
  if (!/CSRF|csrf/.test(peek)) return first;
  _csrfToken = null;
  return send(await fetchCsrfToken(true));
}

export async function postJson<T>(url: string, body?: unknown): Promise<T> {
  return jsonOrThrow<T>(await sendWithCsrf(url, "POST", body));
}

export async function deleteJson<T>(url: string): Promise<T> {
  return jsonOrThrow<T>(await sendWithCsrf(url, "DELETE"));
}
