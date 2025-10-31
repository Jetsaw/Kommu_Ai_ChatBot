  GNU nano 4.8                                 api.js                                            import axios from "axios";
import { readStoredToken } from "./utils/tokenStorage";

const BASE_URL =
  typeof window !== "undefined" && window.VITE_API_BASE
    ? window.VITE_API_BASE
    : import.meta.env?.VITE_API_BASE || "/api";

function requireToken(providedToken) {
  const normalized = (providedToken ?? readStoredToken() ?? "").trim();
  if (!normalized) {
    throw new Error("Missing agent token");
  }
  return normalized;
}

export async function getAgentMe(token) {
  const safeToken = requireToken(token);
  console.log("Calling /api/agent/me with token:", safeToken);
  try {
    const res = await axios.get(`${BASE_URL}/agent/me`, {
      headers: { Authorization: `Bearer ${safeToken}` },
    });
    console.log("/agent/me success:", res.data);
    return res.data;
  } catch (err) {
    console.error("/agent/me failed:", err.response?.data || err.message);
    throw err;
  }
}

export async function getChats(token) {
  const safeToken = requireToken(token);
  console.log("Fetching chat list...");
  try {
    const res = await axios.get(`${BASE_URL}/chats`, {
      headers: { Authorization: `Bearer ${safeToken}` },
    });
    console.log("Chats received:", res.data.length);
    return res.data;
  } catch (err) {
    console.error("Failed to load chats:", err.response?.data || err.message);
    throw err;
  }
}
export async function getChat(token, userId) {
  const safeToken = requireToken(token);
  console.log("Fetching chat history for:", userId);
  try {
    const res = await axios.get(`${BASE_URL}/chat/${userId}`, {
      headers: { Authorization: `Bearer ${safeToken}` },
    });
    console.log("Chat loaded:", res.data.length, "messages");
    return res.data;
  } catch (err) {
    console.error("Failed to get chat:", err.response?.data || err.message);
    throw err;
  }
}

export async function sendMessage(token, userId, content) {
  const safeToken = requireToken(token);
  console.log(`Sending message to ${userId}:`, content);
  try {
    const res = await axios.post(
      `${BASE_URL}/send_message`,
      { user_id: userId, content },
      { headers: { Authorization: `Bearer ${safeToken}` } }
    );
    console.log("Message sent:", res.data);
    return res.data;
  } catch (err) {
    console.error("Failed to send message:", err.response?.data || err.message);
    throw err;
  }
}
console.log("✅ BASE_URL in build:", BASE_URL);
window.__KOMMU_BASE_URL__ = BASE_URL;
