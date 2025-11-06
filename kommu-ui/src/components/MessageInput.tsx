import React, { useState } from "react";

interface MessageInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => Promise<void> | void;
  disabled?: boolean;
}

export default function MessageInput({
  value,
  onChange,
  onSend,
  disabled = false,
}: MessageInputProps) {
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!value.trim() || disabled || sending) return;
    try {
      setSending(true);
      await onSend();
      onChange("");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="border-t border-neutral-200 bg-white px-5 py-3 dark:border-[#1f2c33] dark:bg-[#202c33]">
      <div className="flex items-end gap-3">
        <div className="relative flex-1">
          <textarea
            rows={1}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Type a message…"
            disabled={disabled}
            className={`w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 placeholder-neutral-400 focus:outline-none focus:ring-2 focus:ring-[#2a5cff] dark:border-[#1f2c33] dark:bg-[#111b21] dark:text-[#e9edef] dark:placeholder-[#8696a0] ${
              disabled ? "opacity-60 cursor-not-allowed" : ""
            }`}
          />
          <span className="absolute right-2 bottom-2 text-[11px] text-neutral-400 dark:text-[#8696a0]">
            ↵
          </span>
        </div>
        <button
          onClick={send}
          disabled={disabled || sending}
          className={`rounded-md px-4 py-2 text-sm font-semibold text-white ${
            disabled || sending
              ? "bg-neutral-400 dark:bg-[#2a3942] cursor-not-allowed"
              : "bg-[#2a5cff] hover:opacity-90"
          }`}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
    </div>
  );
}
