import React, { useState } from "react";
import { me } from "../api/backend";
import {
  readStoredToken,
  writeStoredToken,
  clearStoredToken,
} from "../utils/tokenStorage";

export default function Login() {
  
  const [token, setToken] = useState(readStoredToken());
  const [loading, setLoading] = useState(false);

  const onLogin = async () => {
    const normalized = token.trim();
    if (!normalized) {
      alert("Enter a valid token");
      return;
    }
    setLoading(true);
    try {
      
      writeStoredToken(normalized);
      await me();
      window.location.reload();
    } catch {
      alert("Invalid token");
      
      clearStoredToken();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100">
      <div className="w-full max-w-sm bg-slate-900/50 border border-slate-800 p-6 rounded-xl">
        <h1 className="text-xl font-bold mb-2">Kommu CS Dashboard</h1>
        <p className="text-sm text-slate-400 mb-4">Enter your agent token</p>
        <input
          className="w-full p-2 rounded bg-slate-800 border border-slate-700 mb-3"
          placeholder="agent token (e.g. Kommu_123)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <button
          onClick={onLogin}
          disabled={loading}
          className="w-full py-2 rounded bg-blue-600 hover:bg-blue-500 font-semibold disabled:opacity-50"
        >
          
          {loading ? "Checking..." : "Login"}
        </button>
      </div>
    </div>
  );
}