export const TRAINEE_NAME_KEY = "pivot_trainee_name";
export const TRAINEE_SEEN_KEY = "pivot_trainee_seen";
export const TRAINEE_TTL_MS = 60 * 60 * 1000; // 1 hour of inactivity

// Browser-stored trainee id so the same terminal keeps its identity (and its
// server-side persistent crypto mode) across reloads (spec §3.2.1, §3.4.4).
export function traineeId(): string {
  let id = localStorage.getItem("pivot_trainee_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("pivot_trainee_id", id);
  }
  return id;
}

export function saveTrainee(name: string) {
  localStorage.setItem(TRAINEE_NAME_KEY, name);
  localStorage.setItem(TRAINEE_SEEN_KEY, String(Date.now()));
}

export function touchTrainee() {
  if (localStorage.getItem(TRAINEE_NAME_KEY)) {
    localStorage.setItem(TRAINEE_SEEN_KEY, String(Date.now()));
  }
}

export function loadTrainee(): string | null {
  const name = localStorage.getItem(TRAINEE_NAME_KEY);
  const seen = Number(localStorage.getItem(TRAINEE_SEEN_KEY) || 0);
  if (!name || Date.now() - seen > TRAINEE_TTL_MS) return null;
  return name;
}
