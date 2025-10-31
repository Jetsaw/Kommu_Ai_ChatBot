import React, { useState, useEffect, useRef } from "react";
import Login from "./pages/Login";
import ChatList from "./components/ChatList";
import ChatWindow from "./components/ChatWindow";
import MessageInput from "./components/MessageInput";
import Header from "./components/Header";
import Loading from "./components/Loading";
import { getChats, getChat, sendMessage } from "./api/backend";
import { getToken, clearToken } from "./utils/tokenStorage";

export default function App() {
  const [agent, setAgent] = useState("");
  const [token, setToken] = useState(getToken());
  const [chats, setChats] = useState<any[]>([]);
  const [messages, setMessages] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Idle");

  const refreshTimer = useRef<NodeJS.Timeout | null>(null);

  // ⏳ Fetch chats on login
  useEffect(() => {
    if (agent && token) {
      loadChats();
      // Auto-refresh chat list every 10 s
      const listInterval = setInterval(loadChats, 10000);
      return () => clearInterval(listInterval);
    }
  }, [agent, token]);

  // 💬 Load selected chat and refresh messages every 5 s
  useEffect(() => {
    if (selected && token) {
      loadChat(selected);
      refreshTimer.current = setInterval(() => loadChat(selected, true), 5000);
      return () => {
        if (refreshTimer.current) clearInterval(refreshTimer.current);
      };
    }
  }, [selected, token]);

  // === API handlers ===
  const loadChats = async () => {
    try {
      const data = await getChats(token);
      setChats(data);
    } catch (e) {
      console.error("Failed to refresh chats:", e);
    }
  };

  const loadChat = async (userId: string, silent = false) => {
    try {
      if (!silent) setStatus("Loading chat...");
      const data = await getChat(token, userId);
      setMessages(data);
      if (!silent) setStatus("Chat loaded");
    } catch (e) {
      console.error("Error loading chat:", e);
      if (!silent) setStatus("Error");
    }
  };

  const handleSend = async () => {
    if (!msg.trim() || !selected) return;
    setLoading(true);
    setStatus("Sending...");
    try {
      await sendMessage(token, selected, msg);
      setMsg("");
      await loadChat(selected);
      setStatus("Message sent");
    } catch (e) {
      console.error("Failed to send:", e);
      setStatus("Failed to send");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    clearToken();
    setAgent("");
    setToken("");
    setChats([]);
    setMessages([]);
    setSelected(null);
    if (refreshTimer.current) clearInterval(refreshTimer.current);
  };

  // === UI logic ===
  if (!token || !agent)
    return (
      <Login
        onLogin={(n, t) => {
          setAgent(n);
          setToken(t);
        }}
      />
    );

  return (
    <div className="flex flex-col h-screen bg-gray-100">
      <Header name={agent} onLogout={handleLogout} />

      <div className="flex flex-1">
        {/* Left sidebar */}
        <ChatList chats={chats} selected={selected} onSelect={setSelected} />

        {/* Right chat window */}
        {selected ? (
          <div className="flex flex-col flex-1">
            <div className="flex justify-between items-center px-5 py-2 bg-gray-200 border-b">
              <span className="font-semibold text-gray-700">{selected}</span>
              <span className="text-xs text-gray-500">{status}</span>
            </div>

            <ChatWindow messages={messages} agent={agent} />
            <MessageInput
              value={msg}
              onChange={setMsg}
              onSend={handleSend}
              disabled={loading}
            />
          </div>
        ) : (
          <Loading />
        )}
      </div>
    </div>
  );
}
