import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:6090";

export async function getAgentMe(token) {
  const res = await axios.get(`${API_BASE}/api/agents/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

export async function getChats(token) {
  const res = await axios.get(`${API_BASE}/api/chats`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

export async function getChat(token, userId) {
  const res = await axios.get(`${API_BASE}/api/chat/${userId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

export async function sendMessage(token, userId, content) {
  const res = await axios.post(
    `${API_BASE}/api/send_message`,
    { user_id: userId, content },
    { headers: { Authorization: `Bearer ${token}` } }
  );
  return res.data;
}
