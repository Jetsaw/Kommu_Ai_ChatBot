import React, { useState } from "react";

export default function MessageInput({ disabled, onSend }: any) {
  const [text, setText] = useState("");
  const send = async () => {
    if (!text.trim()) return;
    const t = text; setText(""); await onSend(t);
  };
  return (
    <div className="h-14 border-t border-slate-800 flex items-center gap-2 px-3">
      <input
        className="flex-1 h-9 px-3 rounded bg-slate-900/60 border border-slate-700"
        placeholder="Type a message…"
        value={text}
        onChange={(e)=>setText(e.target.value)}
        onKeyDown={(e)=>e.key==="Enter"&&send()}
      />
      <button onClick={send} disabled={disabled} className="h-9 px-4 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50">Send</button>
    </div>
  );
}
