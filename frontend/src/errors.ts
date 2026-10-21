// Keep HTTP status separate from response text so the UI can distinguish a
// rejected password from a lost connection without displaying server internals.
export class ApiError extends Error {
  constructor(public readonly status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

export function isAuthenticationError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function errorMessage(error: unknown, fallback = "The request failed. Please try again."): string {
  if (isAuthenticationError(error)) return "Your instructor session has expired. Sign in again.";
  if (error instanceof ApiError) {
    if (error.status === 429) return "Too many attempts. Wait a moment and try again.";
    if (error.status >= 500) return "The server could not complete the request. Please try again.";
    if (error.status === 400 || error.status === 422) return "The request was rejected. Check your entries and try again.";
  }
  if (error instanceof TypeError || (error instanceof Error && /network|connection|failed to fetch/i.test(error.message))) {
    return "Cannot reach the server. Check your connection and try again.";
  }
  return fallback;
}
