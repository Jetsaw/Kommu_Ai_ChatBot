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
  const [status, setStatus] = useState("Idle");
  const [error, setError] = useState("");

  // ---------------- Agent Auth ----------------
  useEffect(() => {
    if (token) {
      console.log("🔍 Checking agent token:", token);
      setStatus("🔄 Connecting to /api/agent/me ...");
      getAgentMe(token)
        .then((data) => {
          console.log("✅ Agent verified:", data);
          setStatus("✅ Agent verified");
          setAgent(data.name);
          setError("");
          loadChats();
        })
        .catch((err) => {
          console.error("❌ Login error:", err);
          setAgent(null);
          setError(`❌ Login failed: ${err.message || "unknown error"}`);
          setStatus("❌ Unauthorized or server error");
          localStorage.removeItem("agentToken");
        });
    }
  }, [token]);

  async function loadChats() {
    try {
      setStatus("📡 Loading chats...");
      const data = await getChats(token);
      setChats(data);
      setStatus("✅ Chats loaded");
    } catch (err) {
      console.error("❌ Failed to load chats:", err);
      setStatus(`❌ Error loading chats: ${err.message}`);
    }
  }

  async function loadChat(userId) {
    try {
      setStatus(`📥 Loading chat history for ${userId}...`);
      const data = await getChat(token, userId);
      setSelected(userId);
      setMessages(data);
      setStatus("✅ Chat loaded");
    } catch (err) {
      console.error("❌ Failed to load chat:", err);
      setStatus(`❌ Error loading chat: ${err.message}`);
    }
  }

  async function handleSend() {
    if (!content.trim()) return;
    setLoading(true);
    try {
      await sendMessage(token, selected, content);
      setContent("");
      await loadChat(selected);
      setStatus("✅ Message sent");
    } catch (err) {
      console.error(err);
      setStatus(`❌ Failed to send: ${err.message}`);
      alert("Failed to send message");
    } finally {
      setLoading(false);
    }
  }

  async function handleLogin() {
    if (!token.trim()) {
      alert("Enter a valid agent token");
      return;
    }

    console.log("🧠 Attempting login with token:", token);
    setStatus("🔄 Verifying token...");
    try {
      const res = await getAgentMe(token);
      console.log("✅ Login success:", res);
      localStorage.setItem("agentToken", token);
      setAgent(res.name);
      setStatus("✅ Login successful");
      window.location.reload();
    } catch (err) {
      console.error("❌ Login failed:", err);
      setError(`❌ Invalid token: ${err.message}`);
      setStatus("❌ Invalid or unauthorized");
    }
  }

  function handleLogout() {
    localStorage.removeItem("agentToken");
    setToken("");
    setAgent(null);
    setStatus("🚪 Logged out");
  }

  // Auto-refresh chat list every 10 seconds
  useEffect(() => {
    if (agent) {
      const timer = setInterval(loadChats, 10000);
      return () => clearInterval(timer);
    }
  }, [agent]);

  // ---------------- Login Page ----------------
  if (!agent) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-100 text-center">
        <h1 className="text-2xl font-bold text-kommu-blue mb-4">
          Kommu Agent Dashboard (Debug Mode)
        </h1>
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

        {status && (
          <p className="text-gray-700 mt-3 text-sm font-mono">{status}</p>
        )}
        {error && (
          <p className="text-red-600 mt-2 text-sm font-mono">{error}</p>
        )}

        <p className="text-gray-500 text-sm mt-4">
          💡 Tip: Use Agent35 or Agent34 from your .env file
        </p>
      </div>
    );
  }

  // ---------------- Chat Dashboard ----------------
  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Top Bar */}
      <div className="bg-kommu-blue text-white flex justify-between items-center px-6 py-3 shadow">
        <h2 className="font-semibold">👤 Logged in as {agent}</h2>
        <button
          onClick={handleLogout}
          className="bg-white text-kommu-blue px-3 py-1 rounded hover:bg-gray-100"
        >
          Logout
        </button>
      </div>

      <div className="text-center text-sm text-gray-500 py-2 bg-gray-100 border-b">
        {status}
      </div>

      {/* Main Content */}
      <div className="flex flex-1">
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
    </div>
  );
}
