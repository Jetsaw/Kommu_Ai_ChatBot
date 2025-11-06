import React from "react";

interface HeaderProps {
  name: string;
  status?: string;
  syncing?: boolean;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onRefresh: () => void;
  onLogout: () => void;
}

export default function Header({
  name,
  status,
  syncing = false,
  theme,
  onToggleTheme,
  onRefresh,
  onLogout,
}: HeaderProps) {
  return (
    <header className="flex h-14 items-center justify-between border-b border-neutral-200 bg-white px-6 dark:border-[#1f2c33] dark:bg-[#202c33]">
      <div className="flex items-center gap-3">
        <img
          src="/e89b4311-71fa-4716-9eb8-977b8fefcc35.png"
          alt="Kommu"
          className="h-7 w-auto"
        />
        <div>
          <p className="text-sm font-semibold">Kommu Support Dashboard</p>
          <p className="text-[11px] text-neutral-500 dark:text-[#8696a0]">Agent: {name}</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {status && (
          <span
            className={`text-xs ${
              syncing ? "text-emerald-600 dark:text-emerald-300" : "text-neutral-500 dark:text-[#8696a0]"
            }`}
          >
            {syncing ? "Syncing…" : status}
          </span>
        )}

        <button
          onClick={onRefresh}
          className="rounded-md border border-neutral-300 bg-white px-3 py-1 text-sm hover:bg-neutral-50 dark:border-[#1f2c33] dark:bg-[#111b21] dark:hover:bg-[#1a242b]"
          title="Refresh"
        >
          Refresh
        </button>

        <button
          onClick={onToggleTheme}
          className="rounded-md border border-neutral-300 bg-white px-3 py-1 text-sm hover:bg-neutral-50 dark:border-[#1f2c33] dark:bg-[#111b21] dark:hover:bg-[#1a242b]"
          title="Toggle theme"
        >
          {theme === "dark" ? "Light Mode" : "Dark Mode"}
        </button>

        <button
          onClick={onLogout}
          className="rounded-md bg-[#ef4444] px-3 py-1 text-sm font-semibold text-white hover:opacity-90"
        >
          Logout
        </button>
      </div>
    </header>
  );
}
