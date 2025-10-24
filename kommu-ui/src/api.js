import axios from "axios";

const BASE_URL = "/api";


export async function getAgentMe(token) {
  console.log("🔍 Calling /api/agent/me with token:", token);
  try {
    const res = await axios.get(`${BASE_URL}/agent/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    console.log(" /agent/me success:", res.data);
    return res.data;
  } catch (err) {
    console.error(" /agent/me failed:", err.response?.data || err.message);
    throw err;
  }
}


export async function getChats(token) {
  console.log(" Fetching chat list...");
  try {
    const res = await axios.get(`${BASE_URL}/chats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    console.log(" Chats received:", res.data.length);
    return res.data;
  } catch (err) {
    console.error(" Failed to load chats:", err.response?.data || err.message);
    throw err;
  }
}


export async function getChat(token, userId) {
  console.log(" Fetching chat history for:", userId);
  try {
    const res = await axios.get(`${BASE_URL}/chat/${userId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    console.log(" Chat loaded:", res.data.length, "messages");
    return res.data;
  } catch (err) {
    console.error(" Failed to get chat:", err.response?.data || err.message);
    throw err;
  }
}


export async function sendMessage(token, userId, content) {
  console.log(` Sending message to ${userId}:`, content);
  try {
    const res = await axios.post(
      `${BASE_URL}/send_message`,
      { user_id: userId, content },
      { headers: { Authorization: `Bearer ${token}` } }
    );
    console.log(" Message sent:", res.data);
    return res.data;
  } catch (err) {
    console.error(" Failed to send message:", err.response?.data || err.message);
    throw err;
  }
}
