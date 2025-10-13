// utility for calling FastAPI backend
function authHeaders() {
  const t = localStorage.getItem("agent_token") || "";
  return { Authorization: `Bearer ${t}`, "Content-Type": "application/json" };
}

export async function me() {
  const r = await fetch("/api/agents/me", { headers: authHeaders() });
  if (!r.ok) throw new Error("Auth failed");
  return r.json();
}

export async function getChats() {
  const r = await fetch("/api/chats", { headers: authHeaders() });
  return r.json();
}

export async function getChat(user_id: string) {
  const r = await fetch(`/api/chat/${encodeURIComponent(user_id)}`, { headers: authHeaders() });
  return r.json();
}

export async function sendMessage(user_id: string, content: string) {
  const r = await fetch("/api/send_message", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ user_id, content }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
