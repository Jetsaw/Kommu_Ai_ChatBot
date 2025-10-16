import React, { useRef, useEffect } from "react";

export default function ChatWindow({
  messages,
  selected,
  content,
  onChange,
  onSend,
  loading,
}) {
  const scrollRef = useRef();

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (!selected) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500">
        Select a chat to view messages
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 h-full">
      <div className="flex-1 overflow-y-auto p-4 bg-kommu-gray">
        {messages.map((m, idx) => (
          <div
            key={idx}
            className={`mb-3 flex ${
              m.sender === "agent" || m.sender === "bot"
                ? "justify-end"
                : "justify-start"
            }`}
          >
            <div
              className={`rounded-2xl px-4 py-2 max-w-md ${
                m.sender === "agent" || m.sender === "bot"
                  ? "bg-kommu-blue text-white"
                  : "bg-white border border-gray-200"
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}
        <div ref={scrollRef}></div>
      </div>

      <div className="p-3 border-t bg-white flex gap-2">
        <input
          type="text"
          placeholder="Type a message..."
          value={content}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSend()}
          className="flex-1 border border-gray-300 rounded px-3 py-2 focus:outline-none"
        />
        <button
          onClick={onSend}
          disabled={loading}
          className="bg-kommu-blue text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </div>
  );
}
