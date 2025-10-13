import React, { useEffect, useRef } from "react";

interface Message {
  sender: "user" | "bot" | "agent";
  content: string;
}

interface ChatWindowProps {
  messages: Message[];
}

export default function ChatWindow({ messages }: ChatWindowProps) {
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const renderMessage = (msg: Message, i: number) => {
    const isUser = msg.sender === "user";
    const align = isUser ? "justify-start" : "justify-end";
    const bubbleColor = isUser ? "bg-slate-800" : "bg-blue-700";

    // Clean the text to detect markers
    const text = msg.content || "";

    // --------------- IMAGE ---------------
    if (text.includes("[IMAGE]") && text.includes("Saved at:")) {
      const url = text.split("Saved at:")[1].trim();
      return (
        <img
          key={i}
          src={url}
          alt="Received Image"
          className="max-w-[250px] rounded-lg border border-slate-700"
        />
      );
    }

    // --------------- AUDIO ---------------
    if (text.includes("[AUDIO]") && text.includes("Saved at:")) {
      const url = text.split("Saved at:")[1].trim();
      return (
        <audio key={i} controls className="max-w-[250px]">
          <source src={url} type="audio/ogg" />
          Your browser does not support audio playback.
        </audio>
      );
    }

    // --------------- VIDEO ---------------
    if (text.includes("[VIDEO]") && text.includes("Saved at:")) {
      const url = text.split("Saved at:")[1].trim();
      return (
        <video
          key={i}
          controls
          className="max-w-[250px] rounded-lg border border-slate-700"
        >
          <source src={url} type="video/mp4" />
          Your browser does not support video playback.
        </video>
      );
    }

    // --------------- DOCUMENT / PDF ---------------
    if (text.includes("[DOCUMENT]") && text.includes("Saved at:")) {
      const url = text.split("Saved at:")[1].trim();
      const name = url.split("/").pop();
      return (
        <a
          key={i}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-blue-400 underline"
        >
          📎 View Document: {name}
        </a>
      );
    }

    // --------------- FALLBACK: TEXT ---------------
    return (
      <div key={i} className="text-sm whitespace-pre-wrap">
        {text}
      </div>
    );
  };

  return (
    <div
      ref={boxRef}
      className="flex-1 overflow-y-auto p-4 space-y-3 bg-slate-950 text-slate-100"
    >
      {messages.map((msg, i) => (
        <div key={i} className={`flex ${msg.sender === "user" ? "justify-start" : "justify-end"}`}>
          <div
            className={`max-w-[75%] px-3 py-2 rounded-2xl shadow-sm ${msg.sender === "user"
                ? "bg-slate-800 text-slate-100"
                : "bg-blue-700 text-white"
              }`}
          >
            {renderMessage(msg, i)}
          </div>
        </div>
      ))}
    </div>
  );
}
