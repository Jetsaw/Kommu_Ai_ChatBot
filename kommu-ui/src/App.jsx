import React, { useEffect, useState } from "react";
import { getAgentMe, getChats, getChat, sendMessage } from "./api";
import ChatList from "./components/ChatList";
import ChatWindow from "./components/ChatWindow";

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("agentToken") || "");
  const [agent, setAgent] = useState(null);
  const [chats, setChats] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);

  // ---------------- Agent Auth ----------------
  useEffect(() => {
    if (token) {
      getAgentMe(token)
        .then((data) => {
          setAgent(data.name);
          loadChats();
        })
        .catch(() => {
          setAgent(null);
          localStorage.removeItem("agentToken");
        });
    }
  }, [token]);

  async function loadChats() {
    try {
      const data = await getChats(token);
      setChats(data);
    } catch (err) {
      console.error(err);
    }
  }

  async function loadChat(userId) {
    try {
      const data = await getChat(token, userId);
      setSelected(userId);
      setMessages(data);
    } catch (err) {
      console.error(err);
    }
  }

  async function handleSend() {
    if (!content.trim()) return;
    setLoading(true);
    try {
      await sendMessage(token, selected, content);
      setContent("");
      await loadChat(selected);
    } catch (err) {
      console.error(err);
      alert("Failed to send message");
    } finally {
      setLoading(false);
    }
  }

  function handleLogin() {
    if (!token.trim()) return alert("Enter a valid agent token");
    localStorage.setItem("agentToken", token);
    window.location.reload();
  }

  function handleLogout() {
    localStorage.removeItem("agentToken");
    setToken("");
    setAgent(null);
  }

  // Auto-refresh chat list every 10 seconds
  useEffect(() => {
    if (agent) {
      const timer = setInterval(loadChats, 10000);
      return () => clearInterval(timer);
    }
  }, [agent]);

  if (!agent) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-100">
        <h1 className="text-2xl font-bold text-kommu-blue mb-4">Kommu Agent Dashboard</h1>
        <input
          type="text"
          placeholder="Enter Agent Token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="border border-gray-400 rounded p-2 w-64 mb-2"
        />
        <button
          onClick={handleLogin}
          className="bg-kommu-blue text-white px-4 py-2 rounded hover:bg-blue-700"
        >
          Login
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-gray-50">
      <ChatList
        chats={chats}
        selected={selected}
        onSelect={loadChat}
        agent={agent}
        onLogout={handleLogout}
      />
      <ChatWindow
        messages={messages}
        selected={selected}
        content={content}
        onChange={setContent}
        onSend={handleSend}
        loading={loading}
      />
    </div>
  );
}
