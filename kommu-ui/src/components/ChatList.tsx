import React, { useMemo } from "react";

interface ChatSummary {
  user_id: string;
  name?: string;
  profile_pic?: string;
  lastMessage?: string;
  frozen?: boolean;
  lang?: string;
}

interface ChatListProps {
  chats: ChatSummary[];
  selected: string | null;
  onSelect: (userId: string) => void;
  searchTerm: string;
  onSearchTerm: (value: string) => void;
}

export default function ChatList({
  chats,
  selected,
  onSelect,
  searchTerm,
  onSearchTerm,
}: ChatListProps) {
  const filtered = useMemo(() => {
    const term = (searchTerm || "").trim().toLowerCase();
    if (!term) return chats;
    return chats.filter((c) => {
      const n = (c.name || "").toLowerCase();
      const u = (c.user_id || "").toLowerCase();
      const lm = (c.lastMessage || "").toLowerCase();
      return n.includes(term) || u.includes(term) || lm.includes(term);
    });
  }, [chats, searchTerm]);

  return (
    <aside className="w-80 xl:w-96 border-r border-neutral-200 bg-white dark:border-[#1f2c33] dark:bg-[#111b21] flex flex-col">
      <div className="px-4 py-3 border-b border-neutral-200 dark:border-[#1f2c33]">
        <input
          value={searchTerm}
          onChange={(e) => onSearchTerm(e.target.value)}
          placeholder="Search"
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 placeholder-neutral-400 focus:outline-none focus:ring-2 focus:ring-[#2a5cff] dark:border-[#1f2c33] dark:bg-[#202c33] dark:text-[#e9edef] dark:placeholder-[#8696a0]"
        />
      </div>

      <div className="flex-1 overflow-y-auto divide-y divide-neutral-200 dark:divide-[#1f2c33]">
        {filtered.length === 0 && (
          <div className="px-6 py-10 text-center text-sm text-neutral-500 dark:text-[#8696a0]">
            No conversations found
          </div>
        )}

        {filtered.map((c) => {
          const isActive = c.user_id === selected;
          return (
            <button
              key={c.user_id}
              onClick={() => onSelect(c.user_id)}
              className={`w-full px-4 py-3 flex gap-3 text-left hover:bg-neutral-50 dark:hover:bg-[#1a242b] ${
                isActive ? "bg-neutral-50 dark:bg-[#1a242b]" : ""
              }`}
            >
              <img
                src={c.profile_pic || "/default-avatar.png"}
                className="h-11 w-11 rounded-full object-cover bg-neutral-200 dark:bg-[#111b21]"
                alt=""
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between">
                  <p className="truncate text-sm font-semibold">
                    {c.name || c.user_id}
                  </p>
                  <span
                    className={`ml-2 shrink-0 text-[10px] uppercase tracking-widest ${
                      c.frozen ? "text-amber-600 dark:text-amber-300" : "text-neutral-500 dark:text-[#8696a0]"
                    }`}
                  >
                    {c.frozen ? "Human" : c.lang || "Bot"}
                  </span>
                </div>
                <p className="mt-0.5 truncate text-xs text-neutral-500 dark:text-[#8696a0]">
                  {c.lastMessage || "No messages yet"}
                </p>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
