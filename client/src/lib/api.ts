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

async function fetchCsrfToken(): Promise<string> {
  if (_csrfToken) return _csrfToken;
  const res = await fetch("/api/csrf-token", { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  const data = (await res.json()) as { token: string };
  _csrfToken = data.token;
  return data.token;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  const body = text ? safeParse(text) : null;
  if (!res.ok) throw new ApiError(res.status, body ?? text);
  return body as T;
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

export async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const token = await fetchCsrfToken();
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": token,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return jsonOrThrow<T>(res);
}

export async function deleteJson<T>(url: string): Promise<T> {
  const token = await fetchCsrfToken();
  const res = await fetch(url, {
    method: "DELETE",
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-CSRFToken": token },
  });
  return jsonOrThrow<T>(res);
}
