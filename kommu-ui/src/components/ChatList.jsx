import React from "react";

export default function ChatList({ chats, selected, onSelect, agent, onLogout }) {
  return (
    <div className="w-1/3 md:w-1/4 bg-white border-r border-gray-200 flex flex-col">
      {/* Header Section */}
      <div className="p-4 border-b border-gray-200 flex justify-between items-center">
        <h2 className="font-semibold text-kommu-blue">Agent: {agent}</h2>
        <button
          onClick={onLogout}
          className="text-sm text-red-600 hover:underline"
        >
          Logout
        </button>
      </div>

      {/* Chat List */}
      <div className="flex-1 overflow-y-auto">
        {chats.length === 0 && (
          <p className="text-center text-gray-500 mt-10">No active sessions</p>
        )}
        {chats.map((chat) => (
          <div
            key={chat.user_id}
            className={`p-3 cursor-pointer hover:bg-gray-100 ${
              selected === chat.user_id ? "bg-gray-200" : ""
            }`}
            onClick={() => onSelect(chat.user_id)}
          >
            <div className="font-semibold text-gray-800">{chat.user_id}</div>
            <div className="text-gray-500 text-sm truncate">
              {chat.lastMessage}
            </div>
            <div className="text-xs text-gray-400 mt-1">
              {chat.frozen ? "Frozen" : chat.lang === "BM" ? "BM" : "EN"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
