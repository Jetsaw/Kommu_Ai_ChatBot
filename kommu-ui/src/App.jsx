import React, { useState, useEffect } from "react";
import { getChats, getChat, sendMessage, getAgentMe } from "./api";

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("agentToken") || "");
  const [agent, setAgent] = useState(null);
  const [chats, setChats] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("");

  // Verify token and load chats
  useEffect(() => {
    if (!token) return;
    getAgentMe(token)
      .then((d) => {
        setAgent(d.name);
        loadChats();
      })
      .catch(() => {
        localStorage.removeItem("agentToken");
        setAgent(null);
      });
  }, [token]);

  const loadChats = async () => {
    try {
      const data = await getChats(token);
      setChats(data);
      setStatus("Chats loaded");
    } catch (e) {
      setStatus("Error loading chats");
    }
  };

  const loadChat = async (id) => {
    setSelected(id);
    const data = await getChat(token, id);
    setMessages(data);
  };

  const handleSend = async () => {
    if (!input.trim()) return;
    await sendMessage(token, selected, input);
    setInput("");
    await loadChat(selected);
  };

  if (!agent)
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-100">
        <h1 className="text-2xl font-semibold text-kommu-blue mb-4">
          Kommu Agent Dashboard
        </h1>
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Enter agent token"
          className="border px-3 py-2 rounded w-64 mb-3"
        />
        <button
          onClick={() => localStorage.setItem("agentToken", token)}
          className="bg-kommu-blue text-white px-4 py-2 rounded"
        >
          Login
        </button>
      </div>
    );

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <div className="w-1/3 md:w-1/4 bg-white border-r flex flex-col">
        <div className="p-4 border-b flex justify-between items-center">
          <span className="font-semibold text-kommu-blue">
            Agent: {agent}
          </span>
          <button
            onClick={() => {
              localStorage.removeItem("agentToken");
              window.location.reload();
            }}
            className="text-sm text-red-600 hover:underline"
          >
            Logout
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {chats.length === 0 && (
            <p className="text-gray-400 text-center mt-10">
              No active sessions
            </p>
          )}
          {chats.map((c) => (
            <div
              key={c.user_id}
              onClick={() => loadChat(c.user_id)}
              className={`p-3 cursor-pointer hover:bg-gray-100 ${
                selected === c.user_id ? "bg-gray-200" : ""
              }`}
            >
              <div className="font-semibold text-gray-800">{c.user_id}</div>
              <div className="text-gray-500 text-sm truncate">
                {c.lastMessage}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {c.lang === "BM" ? "🇲🇾 BM" : "🇬🇧 EN"}{" "}
                {c.frozen && "🧊 Frozen"}
              </div>
            </div>
          ))}
        </div>

        <div className="text-xs text-gray-500 text-center py-2 border-t">
          {status}
        </div>
      </div>

      {/* Chat Window */}
      <div className="flex flex-col flex-1 bg-kommu-gray">
        {selected ? (
          <>
            <div className="p-3 bg-white border-b font-semibold">
              {selected}
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={`mb-3 flex ${
                    m.sender === "agent"
                      ? "justify-end"
                      : m.sender === "bot"
                      ? "justify-center"
                      : "justify-start"
                  }`}
                >
                  <div
                    className={`rounded-2xl px-4 py-2 max-w-md ${
                      m.sender === "agent"
                        ? "bg-kommu-blue text-white"
                        : m.sender === "bot"
                        ? "bg-gray-300 text-gray-700 text-sm italic"
                        : "bg-white border"
                    }`}
                  >
                    {m.content}
                  </div>
                </div>
              ))}
            </div>

            <div className="p-3 bg-white border-t flex">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Type a message..."
                className="flex-1 border rounded px-3 py-2 mr-2"
                onKeyDown={(e) => e.key === "Enter" && handleSend()}
              />
              <button
                onClick={handleSend}
                className="bg-kommu-blue text-white px-4 py-2 rounded"
              >
                Send
              </button>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-400">
            Select a chat to start messaging
          </div>
        )}
      </div>
    </div>
  );
}
