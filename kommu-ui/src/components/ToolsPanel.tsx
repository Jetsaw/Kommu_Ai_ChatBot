import React from "react";

export default function ToolsPanel({ user_id }: any) {
  return (
    <div className="w-80 border-l border-slate-800 p-3 text-sm text-slate-400">
      <div>User ID: {user_id || "—"}</div>
      <div className="mt-4 italic">Tools (SOP / Warranty) will appear here 🧰</div>
    </div>
  );
}
