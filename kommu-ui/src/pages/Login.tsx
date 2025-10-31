import React, { useState } from "react";
import { getAgentMe } from "../api/backend";
import { saveToken } from "../utils/tokenStorage";

interface Props {
  onLogin: (agent: string, token: string) => void;
}

export default function Login({ onLogin }: Props) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");

  const handleLogin = async () => {
    try {
      const res = await getAgentMe(token.trim());
      saveToken(token.trim());
      onLogin(res.name, token.trim());
    } catch {
      setError("Invalid or unauthorized token");
    }
  };

  return (
    <div className="h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white shadow-lg rounded-2xl p-8 w-96">
        <h1 className="text-2xl font-bold text-center mb-6 text-blue-700">
          Kommu Agent Login
        </h1>
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="border w-full rounded-lg px-3 py-2 mb-4"
          placeholder="Enter Agent Token"
        />
        <button
          onClick={handleLogin}
          className="w-full bg-blue-600 text-white rounded-lg py-2 hover:bg-blue-700"
        >
          Login
        </button>
        {error && <p className="text-red-500 text-sm mt-3 text-center">{error}</p>}
      </div>
    </div>
  );
}
