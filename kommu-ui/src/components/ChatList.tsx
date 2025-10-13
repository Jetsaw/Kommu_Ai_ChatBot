import React from "react";

export default function ChatList({ chats, active, onPick }: any) {
  return (
    <div className="w-72 border-r border-slate-800 overflow-y-auto">
      {chats.map((c: any) => (
        <button
          key={c.user_id}
          onClick={() => onPick(c.user_id)}
          className={`w-full text-left p-3 border-b border-slate-800 hover:bg-slate-900 ${active===c.user_id?"bg-slate-900":""}`}
        >
          <div className="font-semibold">{c.user_id}</div>
          <div className="text-xs text-slate-400 truncate">{c.lastMessage}</div>
        </button>
      ))}
    </div>
  );
}
