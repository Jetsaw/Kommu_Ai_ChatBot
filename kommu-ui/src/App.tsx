import React, { useEffect, useState, useCallback, useRef } from "react";
import { getToken, setToken, clearToken } from "./utils/tokenStorage";

const API_BASE = import.meta.env.VITE_API_BASE || "https://api.kommu.ai/api";

export default function App() {
  const [token, setTok] = useState(getToken());
  const [loginInput, setLoginInput] = useState("");
  const [agent, setAgent] = useState("");
  const [chats, setChats] = useState<any[]>([]);
  const [messages, setMessages] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("");
  const [darkMode, setDarkMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [botTyping, setBotTyping] = useState(false);

  // rename
  const [renameMode, setRenameMode] = useState(false);
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // scrolling / unread
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isLoadingOld, setIsLoadingOld] = useState(false);
  const [visibleCount, setVisibleCount] = useState(40);
  const [totalMessageCount, setTotalMessageCount] = useState(0);
  const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});
  const [searchQuery, setSearchQuery] = useState("");
  const [showScrollButton, setShowScrollButton] = useState(false);

  // ---------------- Verify agent ----------------
  useEffect(() => {
    if (token) verifyAgent(token);
  }, [token]);

  const verifyAgent = async (tok: string) => {
    try {
      const res = await fetch(`${API_BASE}/agent/me`, {
        headers: { Authorization: `Bearer ${tok}` },
      });
      if (!res.ok) throw new Error("Invalid token");

      const data = await res.json();
      setAgent(data.name);
      setToken(tok);

      loadChats(tok);
    } catch {
      clearToken();
      setTok(null);
      setStatus("Invalid token. Please re-login.");
    }
  };

  // ---------------- Load chat list ----------------
  const loadChats = useCallback(
    async (tok?: string) => {
      try {
        const res = await fetch(`${API_BASE}/chats`, {
          headers: { Authorization: `Bearer ${tok || token}` },
        });

        const data = await res.json();
        setChats([...data].reverse());
      } catch (e) {
        console.error("loadChats error:", e);
      }
    },
    [token]
  );

  // ---------------- Load chat messages ----------------
  const loadChat = useCallback(
    async (userId: string, silent = false) => {
      try {
        if (!silent) {
          setSelected(userId);
          setVisibleCount(40);
          setUnreadCounts((prev) => ({ ...prev, [userId]: 0 }));
        }

        const res = await fetch(`${API_BASE}/chat/${userId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });

        const data = await res.json();
        setTotalMessageCount(data.length);

        const slice = data.slice(-visibleCount);

        setMessages(
          slice.map((m: any) => ({
            sender: m.sender,
            content: m.content,
            time:
              m.time ||
              m.timestamp ||
              m.created_at ||
              m.sent_at ||
              new Date().toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              }),
          }))
        );

        scrollBottom(!silent);
      } catch (e) {
        console.error("loadChat error:", e);
      }
    },
    [token, visibleCount]
  );

  // ---------------- Scroll to bottom ----------------
  const scrollBottom = (force = false) => {
    const el = scrollRef.current;
    if (!el) return;

    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    if (force || nearBottom) {
      setTimeout(() => (el.scrollTop = el.scrollHeight), 100);
    }
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 300;
      setShowScrollButton(!nearBottom);
    };

    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  // ---------------- Send message ----------------
  const sendMessage = async () => {
    if (!input.trim() || !selected) return;

    setLoading(true);
    setBotTyping(true);

    try {
      await fetch(`${API_BASE}/send_message`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ user_id: selected, content: input }),
      });

      setInput("");
      await loadChat(selected, true);
      setTimeout(() => setBotTyping(false), 1500);
    } catch (e) {
      console.error("sendMessage:", e);
    } finally {
      setLoading(false);
    }
  };

  // ---------------- Mode toggle ----------------
  const setChatMode = async (mode: "bot" | "human") => {
    if (!selected) {
      setStatus("No chat selected");
      return;
    }

    try {
      const endpoint = mode === "human" ? "/freeze" : "/unfreeze";

      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ user_id: selected }),
      });

      if (!res.ok) {
        let reason = "";
        try {
          const err = await res.json();
          reason = err.error ? ` (${err.error})` : "";
        } catch {}
        setStatus(`Failed to toggle chat mode [${res.status}${reason}]`);
        return;
      }

      const data = await res.json();

      if (data.status === "frozen") setStatus(" Live agent takeover (Human mode)");
      else if (data.status === "unfrozen") setStatus(" Chatbot resumed (Bot mode)");
      else setStatus("Mode updated");

      await loadChats();
      await loadChat(selected, true);
    } catch (e: any) {
      setStatus(`Error toggling chat mode: ${e.message || e}`);
    }
  };

  // ---------------- Auto refresh ----------------
  useEffect(() => {
    if (!selected) return;

    const interval = setInterval(async () => {
      const res = await fetch(`${API_BASE}/chat/${selected}`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      const data = await res.json();
      setTotalMessageCount(data.length);

      const slice = data.slice(-visibleCount);

      const last = data[data.length - 1];
      if (last?.sender === "user" && !document.hasFocus()) {
        setUnreadCounts((prev) => ({
          ...prev,
          [selected]: (prev[selected] || 0) + 1,
        }));
      }

      setMessages(
        slice.map((m: any) => ({
          sender: m.sender,
          content: m.content,
          time:
            m.time ||
            m.timestamp ||
            m.created_at ||
            m.sent_at ||
            new Date().toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            }),
        }))
      );
    }, 4000);

    return () => clearInterval(interval);
  }, [selected, token, visibleCount]);

  // ---------------- SCROLL-UP PATCH (FINAL) ----------------
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !selected) return;

    let locked = false;

    const handleScroll = async () => {
      const top = el.scrollTop;
      const atTop = top <= 5;

      if (atTop && !isLoadingOld && !locked) {
        locked = true;
        await loadOlderMessages();
        setTimeout(() => (locked = false), 250);
      }
    };

    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, [selected, token, visibleCount, isLoadingOld]);

  // ---------------- Load older messages ----------------
  const loadOlderMessages = async () => {
    if (!selected) return;

    setIsLoadingOld(true);
    const el = scrollRef.current;
    const prevScrollHeight = el?.scrollHeight || 0;

    try {
      const res = await fetch(`${API_BASE}/chat/${selected}`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      const data = await res.json();
      setTotalMessageCount(data.length);

      const newCount = Math.min(visibleCount + 40, data.length);

      const slice = data.slice(-newCount).map((m: any) => ({
        sender: m.sender,
        content: m.content,
        time:
          m.time ||
          m.timestamp ||
          m.created_at ||
          m.sent_at ||
          new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          }),
      }));

      setVisibleCount(newCount);
      setMessages(slice);

      requestAnimationFrame(() => {
        if (el) {
          const diff = el.scrollHeight - prevScrollHeight;
          el.scrollTop = diff + el.scrollTop;
        }
      });
    } catch (e) {
      console.error("older messages error:", e);
    } finally {
      setIsLoadingOld(false);
    }
  };

  // ---------------- Login screen ----------------
  if (!token)
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-gray-100">
        <img src="/kommu_logo.png" alt="Kommu Logo" className="w-40 mb-6 object-contain" />
        <div className="bg-white p-6 rounded-xl shadow-md w-80">
          <h2 className="text-lg font-semibold mb-3 text-center">Kommu CS Dashboard</h2>
          <input
            className="border w-full p-2 rounded mb-2"
            placeholder="Enter your agent token"
            value={loginInput}
            onChange={(e) => setLoginInput(e.target.value)}
          />
          <button
            className="bg-blue-600 hover:bg-blue-700 text-white py-2 px-4 rounded w-full"
            onClick={() => {
              setTok(loginInput);
              verifyAgent(loginInput);
            }}
          >
            Login
          </button>
          {status && <p className="text-center text-sm text-red-500 mt-2">{status}</p>}
        </div>
      </div>
    );
// ---------------- Main UI ----------------
return (
  <div
    className={`flex h-full max-h-screen overflow-hidden transition-colors duration-300 ${
      darkMode ? "bg-slate-900 text-white" : "bg-gray-100 text-gray-900"
    }`}
  >
    {/* Sidebar */}
    <div
      className={`w-72 border-r flex flex-col ${
        darkMode ? "border-slate-700 bg-slate-800" : "border-gray-300 bg-white"
      }`}
    >
      <div className="p-5 flex items-center justify-center border-b">
        <img src="/kommu_logo.png" alt="Kommu Logo" className="w-28" />
      </div>

      <div className="p-3 flex justify-between items-center border-b">
        <div>
          <h2 className="font-semibold">Welcome, {agent}</h2>
          <button
            onClick={() => {
              clearToken();
              setTok(null);
            }}
            className="text-xs text-red-500 hover:underline"
          >
            Logout
          </button>
        </div>

        <button
          onClick={() => setDarkMode(!darkMode)}
          className={`text-xs px-3 py-1 rounded ${
            darkMode ? "bg-slate-700 text-white" : "bg-gray-200 text-gray-800"
          }`}
        >
          {darkMode ? "Light" : "Dark"}
        </button>
      </div>

      {/* Search */}
      <div className="p-2 border-b">
        <input
          type="text"
          placeholder="Search chats..."
          className={`w-full text-sm p-2 rounded border ${
            darkMode
              ? "bg-slate-700 border-slate-600 text-white"
              : "bg-white border-gray-300 text-gray-800"
          }`}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>

      {/* Chat list */}
      <div className="flex-1 overflow-y-auto h-full">
        {chats
          .filter(
            (c) =>
              !searchQuery ||
              c.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
              c.user_id.includes(searchQuery)
          )
          .map((c) => (
            <div
              key={c.user_id}
              onClick={() => loadChat(c.user_id)}
              className={`cursor-pointer flex items-center gap-3 p-2 border-b ${
                selected === c.user_id
                  ? darkMode
                    ? "bg-slate-700"
                    : "bg-blue-100"
                  : darkMode
                  ? "hover:bg-slate-700/50"
                  : "hover:bg-gray-100"
              }`}
            >
              {/* Avatar */}
              {c.profile_pic ? (
                <img src={c.profile_pic} className="w-10 h-10 rounded-full object-cover" />
              ) : (
                <div
                  className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold border ${
                    darkMode
                      ? "border-slate-600 bg-slate-700"
                      : "border-gray-300 bg-gray-100"
                  }`}
                >
                  {c.name ? c.name[0].toUpperCase() : "?"}
                </div>
              )}

              {/* Chat info */}
              <div className="flex-1 min-w-0">
                <div className="font-semibold truncate">{c.name}</div>
                <div className="flex justify-between items-center text-xs opacity-70">
                  <span className="truncate">{c.lastMessage || "No messages yet"}</span>
                  {c.lastMessageTime && (
                    <span className="ml-2 whitespace-nowrap">
                      {new Date(c.lastMessageTime).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  )}
                </div>
              </div>

              {/* Unread count */}
              {unreadCounts[c.user_id] > 0 && (
                <span className="bg-blue-600 text-white text-xs font-bold rounded-full px-2 py-0.5">
                  {unreadCounts[c.user_id]}
                </span>
              )}

              <span
                className={`text-xs font-medium ${
                  c.frozen ? "text-amber-500" : "text-green-500"
                }`}
              >
                {c.frozen ? "Human" : "Bot"}
              </span>
            </div>
          ))}
      </div>
    </div>

    {/* Chat Area */}
    <div className="flex-1 flex flex-col relative h-full">

      {/* Header */}
      <div className="flex justify-between items-center p-3 border-b">
        <h2 className="font-semibold">
          {selected
            ? chats.find((c) => c.user_id === selected)?.name || selected
            : "Select a chat"}
        </h2>

        {selected && (
          <div className="flex flex-col items-end gap-1">
            <div className="flex gap-2 items-center">
              <button
                onClick={() => setChatMode("human")}
                className="bg-amber-500 hover:bg-amber-600 text-white px-3 py-1 rounded text-xs"
              >
                Live Agent
              </button>

              <button
                onClick={() => setChatMode("bot")}
                className="bg-green-600 hover:bg-green-700 text-white px-3 py-1 rounded text-xs"
              >
                Resume Bot
              </button>
            </div>

            {status && (
              <span className="text-[11px] text-gray-500 italic">{status}</span>
            )}
          </div>
        )}
      </div>

      {/* Messages */}
      <div
        id="chat-scroll"
        ref={scrollRef}
        className={`overflow-y-auto p-6 space-y-4 h-[calc(100vh-190px)] ${
          darkMode ? "bg-slate-900" : "bg-gray-50"
        }`}
      >
        {/* Loading older */}
        {isLoadingOld && (
          <div className="text-center text-xs text-gray-500 my-2">
            Loading older messages…
          </div>
        )}

        {/* Load previous button */}
        {!isLoadingOld &&
          messages.length > 0 &&
          visibleCount < totalMessageCount && (
            <div className="flex justify-center mb-2">
              <button
                onClick={loadOlderMessages}
                className="text-xs text-blue-600 hover:underline"
              >
                ⬆ Load previous messages
              </button>
            </div>
          )}

        {/* Message bubbles */}
        {messages.map((m, i) => {
          const isUser = m.sender === "user";
          const isAgent = m.sender === "agent";
          const isBot = m.sender === "bot";

          const align = isUser ? "justify-start" : "justify-end";

          const bubbleColor = isAgent
            ? "bg-blue-600 text-white"
            : isBot
            ? "bg-emerald-200 text-emerald-900"
            : "bg-gray-200 text-gray-900";

          const content = renderMessageContent(m.content, i, darkMode);

          return (
            <div key={i} className={`flex ${align} my-2`}>
              <div className={`max-w-[75%] rounded-2xl px-4 py-2 shadow-sm ${bubbleColor}`}>
                <div className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                  {content}
                </div>
                <div
                  className={`text-[10px] mt-1 text-right ${
                    darkMode ? "text-gray-400" : "text-gray-500"
                  }`}
                >
                  {m.time}
                </div>
              </div>
            </div>
          );
        })}

        {botTyping && (
          <div className="flex justify-end">
            <div
              className={`px-4 py-2 rounded-2xl text-sm ${
                darkMode
                  ? "bg-emerald-400/20 text-emerald-200"
                  : "bg-emerald-100 text-emerald-800"
              }`}
            >
              <span className="animate-pulse">Kai is typing…</span>
            </div>
          </div>
        )}
      </div>

      {/* Scroll bottom */}
      {showScrollButton && (
        <button
          onClick={() => scrollBottom(true)}
          className="absolute bottom-24 right-6 bg-blue-600 text-white p-2 rounded-full shadow-lg hover:bg-blue-700 transition-colors"
        >
          ↓
        </button>
      )}

      {/* Input box */}
      {selected && (
        <div
          className={`flex items-center gap-2 border-t p-3 ${
            darkMode ? "border-slate-700 bg-slate-800" : "border-gray-300 bg-white"
          }`}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && sendMessage()}
            placeholder="Type a message..."
            className={`flex-1 p-2 rounded border text-sm ${
              darkMode
                ? "bg-slate-700 border-slate-600 text-white"
                : "bg-white border-gray-300 text-gray-900"
            }`}
            disabled={loading}
          />

          <button
            onClick={sendMessage}
            disabled={loading}
            className={`px-4 py-2 rounded text-white ${
              loading
                ? "bg-gray-400 cursor-not-allowed"
                : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            Send
          </button>
        </div>
      )}
    </div>
  </div>
);
}

/* ---------------- Media Renderer ---------------- */
function renderMessageContent(raw: string, i: number, darkMode: boolean) {
  const text = raw || "";
  const API_BASE = import.meta.env.VITE_API_BASE || "https://api.kommu.ai/api";

  const mediaUrl = () =>
    text.split("Saved at:")[1]?.trim().replace("/app", API_BASE) || "";

  // IMAGE
  if (text.includes("[IMAGE]") && text.includes("Saved at:")) {
    const url = mediaUrl();
    return (
      <img
        key={`img-${i}`}
        src={url}
        alt="Image"
        className="max-w-[220px] rounded-lg border border-gray-400/40"
      />
    );
  }

  // AUDIO
  if (text.includes("[AUDIO]") && text.includes("Saved at:")) {
    const url = mediaUrl();
    return (
      <audio key={`aud-${i}`} controls className="max-w-[220px]">
        <source src={url} type="audio/ogg" />
        Audio not supported.
      </audio>
    );
  }

  // VIDEO
  if (text.includes("[VIDEO]") && text.includes("Saved at:")) {
    const url = mediaUrl();
    return (
      <video
        key={`vid-${i}`}
        controls
        className="max-w-[240px] rounded-lg border border-gray-400/40"
      >
        <source src={url} type="video/mp4" />
        Video not supported.
      </video>
    );
  }

  // DOCUMENT
  if (text.includes("[DOCUMENT]") && text.includes("Saved at:")) {
    const url = mediaUrl();
    const name = url.split("/").pop();
    return (
      <a
        key={`doc-${i}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="underline text-blue-500"
      >
        📎 {name}
      </a>
    );
  }

  // DEFAULT TEXT
  return text;
}

