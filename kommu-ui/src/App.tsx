import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Header from "./components/Header";
import ChatList from "./components/ChatList";
import ChatWindow from "./components/ChatWindow";
import MessageInput from "./components/MessageInput";
import { getToken, setToken as saveToken, clearToken } from "./utils/tokenStorage";

type ChatSummary = {
  user_id: string;
  name?: string;
  profile_pic?: string;
  lastMessage?: string;
  frozen?: boolean;
  lang?: string;
};

type ChatMessage = {
  sender: "user" | "bot" | "agent";
  content: string;
};

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export default function App() {
  const [token, setToken] = useState<string | null>(getToken());
  const [agentName, setAgentName] = useState<string>("");
  const [status, setStatus] = useState<string>("Checking access…");
  const [syncing, setSyncing] = useState<boolean>(false);

  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const [searchTerm, setSearchTerm] = useState<string>("");
  const [draft, setDraft] = useState<string>("");

  const [theme, setTheme] = useState<"light" | "dark">(
    (localStorage.getItem("kommu_theme") as "light" | "dark") || "light"
  );

  const listTimerRef = useRef<number | null>(null);
  const chatTimerRef = useRef<number | null>(null);

  // ---------- Theme ----------
  useEffect(() => {
    localStorage.setItem("kommu_theme", theme);
    const root = document.documentElement;
    if (theme === "dark") root.classList.add("dark");
    else root.classList.remove("dark");
  }, [theme]);

  // ---------- Verify token ----------
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        setStatus("Verifying…");
        const res = await fetch(`${API_BASE}/agent/me`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error("Unauthorized");
        const data = await res.json();
        if (cancelled) return;
        setAgentName(data.name || "Agent");
        setStatus("Ready");
        await loadChats();
      } catch {
        if (!cancelled) {
          setStatus("Authentication failed");
          handleLogout();
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // ---------- Load chats ----------
  const loadChats = useCallback(async () => {
    if (!token) return;
    setSyncing(true);
    try {
      const res = await fetch(`${API_BASE}/chats`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = (await res.json()) as ChatSummary[];
      setChats(data);
      setStatus(`Updated ${new Date().toLocaleTimeString()}`);
    } catch (e) {
      setStatus("Failed to refresh chats");
      // console.error(e);
    } finally {
      setSyncing(false);
    }
  }, [token]);

  // ---------- Load single chat ----------
  const loadChat = useCallback(
    async (userId: string, silent = false) => {
      if (!token) return;
      try {
        if (!silent) setStatus("Loading conversation…");
        const res = await fetch(`${API_BASE}/chat/${userId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const data = (await res.json()) as ChatMessage[];
        setMessages(data);
        if (!silent) setStatus("Conversation loaded");
      } catch {
        if (!silent) setStatus("Failed to load conversation");
      }
    },
    [token]
  );

  // ---------- Send message ----------
  const handleSend = useCallback(async () => {
    if (!token || !selected || !draft.trim()) return;
    setStatus("Sending…");
    try {
      const res = await fetch(`${API_BASE}/send_message`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ user_id: selected, content: draft.trim() }),
      });
      if (res.ok) {
        setDraft("");
        await loadChat(selected);
        setStatus("Sent");
      } else {
        setStatus("Failed to send");
      }
    } catch {
      setStatus("Failed to send");
    }
  }, [token, selected, draft, loadChat]);

  // ---------- Freeze / Resume via messages ----------
  const handleFreeze = useCallback(async () => {
    // Send the conventional trigger "LA" — your webhook can freeze on this
    if (!selected) return;
    setDraft("LA");
    await handleSend();
  }, [selected, handleSend]);

  const handleResume = useCallback(async () => {
    // Your webhook already supports "resume" to unfreeze
    if (!selected) return;
    setDraft("resume");
    await handleSend();
  }, [selected, handleSend]);

  // ---------- Intervals ----------
  useEffect(() => {
    if (!token || !agentName) return;
    loadChats();
    if (listTimerRef.current) window.clearInterval(listTimerRef.current);
    listTimerRef.current = window.setInterval(loadChats, 15000);
    return () => {
      if (listTimerRef.current) window.clearInterval(listTimerRef.current);
      listTimerRef.current = null;
    };
  }, [token, agentName, loadChats]);

  useEffect(() => {
    if (!token || !selected) return;
    loadChat(selected);
    if (chatTimerRef.current) window.clearInterval(chatTimerRef.current);
    chatTimerRef.current = window.setInterval(() => loadChat(selected, true), 5000);
    return () => {
      if (chatTimerRef.current) window.clearInterval(chatTimerRef.current);
      chatTimerRef.current = null;
    };
  }, [token, selected, loadChat]);

  // ---------- Logout ----------
  const handleLogout = useCallback(() => {
    clearToken();
    setToken(null);
    setAgentName("");
    setChats([]);
    setMessages([]);
    setSelected(null);
    setDraft("");
  }, []);

  // ---------- Login Screen ----------
  if (!token) {
    const [tmpToken, setTmpToken] = useState<string>("");
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-50 dark:bg-[#0b141a]">
        <div className="w-[380px] rounded-xl border border-neutral-200 dark:border-[#1f2c33] bg-white dark:bg-[#111b21] p-6 shadow-sm">
          <h1 className="text-xl font-semibold text-neutral-900 dark:text-white text-center">
            Kommu Support Dashboard
          </h1>
          <p className="text-xs text-neutral-500 dark:text-[#8696a0] text-center mt-1">
            Enter your agent token to continue
          </p>
          <div className="mt-4 space-y-3">
            <input
              className="w-full rounded-lg border border-neutral-300 dark:border-[#1f2c33] bg-white dark:bg-[#111b21] px-3 py-2 text-sm text-neutral-900 dark:text-[#e9edef] focus:outline-none focus:ring-2 focus:ring-[#2a5cff]"
              placeholder="Agent token"
              value={tmpToken}
              onChange={(e) => setTmpToken(e.target.value)}
            />
            <button
              className="w-full rounded-lg bg-[#2a5cff] py-2 text-sm font-semibold text-white hover:opacity-90"
              onClick={async () => {
                if (!tmpToken.trim()) return;
                try {
                  const res = await fetch(`${API_BASE}/agent/me`, {
                    headers: { Authorization: `Bearer ${tmpToken.trim()}` },
                  });
                  if (!res.ok) throw new Error("Unauthorized");
                  const data = await res.json();
                  saveToken(tmpToken.trim());
                  setToken(tmpToken.trim());
                  setAgentName(data.name || "Agent");
                } catch {
                  alert("Invalid token");
                }
              }}
            >
              Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

  const activeChat = useMemo(
    () => chats.find((c) => c.user_id === selected) || null,
    [chats, selected]
  );

  return (
    <div className="flex h-screen bg-neutral-50 text-neutral-900 dark:bg-[#0b141a] dark:text-[#e9edef]">
      <Header
        name={agentName}
        status={status}
        syncing={syncing}
        theme={theme}
        onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
        onLogout={handleLogout}
        onRefresh={loadChats}
      />

      <div className="flex flex-1 overflow-hidden">
        <ChatList
          chats={chats}
          selected={selected}
          onSelect={setSelected}
          searchTerm={searchTerm}
          onSearchTerm={setSearchTerm}
        />

        <main className="flex-1 flex flex-col">
          {selected && activeChat ? (
            <>
              {/* Chat header strip */}
              <div className="px-6 py-3 border-b border-neutral-200 dark:border-[#1f2c33] bg-white dark:bg-[#202c33]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <img
                      src={activeChat.profile_pic || "/default-avatar.png"}
                      className="h-10 w-10 rounded-full object-cover bg-neutral-200 dark:bg-[#111b21]"
                      alt=""
                    />
                    <div>
                      <p className="text-sm font-semibold">
                        {activeChat.name || activeChat.user_id}
                      </p>
                      <p className="text-xs text-neutral-500 dark:text-[#8696a0]">
                        {activeChat.user_id}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                        activeChat.frozen
                          ? "bg-amber-100 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300"
                          : "bg-emerald-100 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300"
                      }`}
                    >
                      {activeChat.frozen ? "Human takeover active" : "Chatbot responding"}
                    </span>
                    <button
                      onClick={handleFreeze}
                      className="rounded-md border border-neutral-300 dark:border-[#1f2c33] bg-white dark:bg-[#111b21] px-3 py-1 text-sm hover:bg-neutral-50 dark:hover:bg-[#1a242b]"
                      disabled={false}
                      title="Send LA to freeze chatbot"
                    >
                      Live Agent
                    </button>
                    <button
                      onClick={handleResume}
                      className="rounded-md bg-[#2a5cff] px-3 py-1 text-sm font-semibold text-white hover:opacity-90"
                      title="Send resume to unfreeze"
                    >
                      Resume Bot
                    </button>
                  </div>
                </div>
              </div>

              <ChatWindow messages={messages} />

              <MessageInput
                value={draft}
                onChange={setDraft}
                onSend={handleSend}
                disabled={false /* agent can always reply */}
              />
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center">
              <div className="max-w-lg text-center">
                <h2 className="text-xl font-semibold">Select a conversation</h2>
                <p className="mt-2 text-sm text-neutral-500 dark:text-[#8696a0]">
                  Pick a chat from the left to review and reply. Use the “Live Agent” button to
                  request takeover (LA), and “Resume Bot” to continue automation.
                </p>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
