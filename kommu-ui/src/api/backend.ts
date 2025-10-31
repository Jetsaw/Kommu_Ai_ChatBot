import axios from "axios";
import { getToken } from "../utils/tokenStorage";

const BASE_URL = import.meta.env.VITE_API_BASE || "/api";

function authHeaders(token?: string) {
  const t = token || getToken();
  return { Authorization: `Bearer ${t}` };
}

export async function getAgentMe(token?: string) {
  const res = await axios.get(`${BASE_URL}/agent/me`, {
    headers: authHeaders(token),
  });
  return res.data;
}

export async function getChats(token?: string) {
  const res = await axios.get(`${BASE_URL}/chats`, {
    headers: authHeaders(token),
  });
  return res.data;
}

export async function getChat(token: string, userId: string) {
  const res = await axios.get(`${BASE_URL}/chat/${userId}`, {
    headers: authHeaders(token),
  });
  return res.data;
}

export async function sendMessage(
  token: string,
  userId: string,
  content: string
) {
  const res = await axios.post(
    `${BASE_URL}/send_message`,
    { user_id: userId, content },
    { headers: authHeaders(token) }
  );
  return res.data;
}
