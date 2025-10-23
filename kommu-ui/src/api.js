import axios from "axios";

const BASE_URL = "/api";

export async function getAgentMe(token) {
  const res = await axios.get(`${BASE_URL}/agents/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

export async function getChats(token) {
  const res = await axios.get(`${BASE_URL}/chats`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

export async function getChat(token, userId) {
  const res = await axios.get(`${BASE_URL}/chat/${userId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

export async function sendMessage(token, userId, content) {
  const res = await axios.post(
    `${BASE_URL}/send_message`,
    { user_id: userId, content },
    { headers: { Authorization: `Bearer ${token}` } }
  );
  return res.data;
}
