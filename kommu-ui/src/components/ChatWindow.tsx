import React, { useEffect, useMemo, useRef } from "react";

type Sender = "user" | "bot" | "agent";

interface Message {
  sender: Sender;
  content: string;
}

interface ChatWindowProps {
  messages: Message[];
}

function renderContent(message: Message, index: number) {
  const text = message.content || "";

  // IMAGE
  if (text.includes("[IMAGE]") && text.includes("Saved at:")) {
    const url = text.split("Saved at:")[1].trim();
    return (
      <img
        key={`img-${index}`}
        src={url}
        alt="Received"
        className="max-w-[260px] rounded-lg border border-neutral-200 dark:border-[#1f2c33]"
      />
    );
  }

  // AUDIO
  if (text.includes("[AUDIO]") && text.includes("Saved at:")) {
    const url = text.split("Saved at:")[1].trim();
    return (
      <audio key={`audio-${index}`} controls className="max-w-[260px]">
        <source src={url} type="audio/ogg" />
      </audio>
    );
  }

  // VIDEO
  if (text.includes("[VIDEO]") && text.includes("Saved at:")) {
    const url = text.split("Saved at:")[1].trim();
    return (
      <video
        key={`video-${index}`}
        controls
        className="max-w-[260px] rounded-lg border border-neutral-200 dark:border-[#1f2c33]"
      >
        <source src={url} type="video/mp4" />
      </video>
    );
  }

  // DOCUMENT
  if (text.includes("[DOCUMENT]") && text.includes("Saved at:")) {
    const url = text.split("Saved at:")[1].trim();
    const name = url.split("/").pop();
    return (
      <a
        key={`doc-${index}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm underline text-[#2a5cff]"
      >
        View document: {name}
      </a>
    );
  }

  return <span key={`text-${index}`}>{text}</span>;
}

export default function ChatWindow({ messages }: ChatWindowProps) {
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.scrollTo({
      top: boxRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  const rendered = useMemo(
    () =>
      messages.map((m, i) => {
        const align =
          m.sender === "agent"
            ? "justify-end"
            : m.sender === "bot"
            ? "justify-center"
            : "justify-start";

        const bubble =
          m.sender === "agent"
            ? "bg-[#2a5cff] text-white"
            : m.sender === "user"
            ? "bg-neutral-100 text-neutral-900 dark:bg-[#202c33] dark:text-[#e9edef]"
            : "bg-neutral-200 text-neutral-900 dark:bg-[#233138] dark:text-[#c6d4db]";

        return (
          <div key={i} className={`flex ${align}`}>
            <div className={`max-w-[76%] rounded-2xl px-4 py-3 shadow-sm ${bubble}`}>
              <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                {renderContent(m, i)}
              </div>
            </div>
          </div>
        );
      }),
    [messages]
  );

  return (
    <div
      ref={boxRef}
      className="flex-1 overflow-y-auto px-6 py-5 space-y-3 bg-white dark:bg-[#0b141a]"
    >
      {rendered}
    </div>
  );
}
