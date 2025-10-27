import { readStoredToken } from "../utils/tokenStorage";

const BASE_URL = import.meta.env.VITE_API_BASE ?? "/api";

function requireToken(): string {
  const token = readStoredToken().trim();
  if (!token) {
    throw new Error("Missing agent token");
  }
  return token;
}

function authHeaders() {
  
  const t = requireToken();
  return { Authorization: `Bearer ${t}`, "Content-Type": "application/json" };
}

export async function me() {
  
  const r = await fetch(`${BASE_URL}/agent/me`, { headers: authHeaders() });
  if (!r.ok) throw new Error("Auth failed");
  return r.json();
}

export async function getChats() {
  
  const r = await fetch(`${BASE_URL}/chats`, { headers: authHeaders() });
  return r.json();
}

export async function getChat(user_id: string) {
  
  const r = await fetch(`${BASE_URL}/chat/${encodeURIComponent(user_id)}`, {
    headers: authHeaders(),
  });
  return r.json();
}

export async function sendMessage(user_id: string, content: string) {
  
  const r = await fetch(`${BASE_URL}/send_message`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ user_id, content }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}