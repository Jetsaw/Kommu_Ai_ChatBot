import React from "react";
import { clearStoredToken } from "../utils/tokenStorage";

export default function HeaderBar() {
  
  const logout = () => {
    clearStoredToken();
    window.location.reload();
  };
  return (
    <div className="h-12 flex items-center justify-between px-4 border-b border-slate-800 bg-slate-900/60">
      <div className="font-semibold">Kommu CS</div>
      <button onClick={logout} className="px-3 py-1 rounded bg-slate-700 hover:bg-slate-600">Logout</button>
    </div>
  );
}