import React, { useEffect, useState } from "react";
import HeaderBar from "../components/Header";
import ChatList from "../components/ChatList";
import ChatWindow from "../components/ChatWindow";
import MessageInput from "../components/MessageInput";
import ToolsPanel from "../components/ToolsPanel";
import { getChats, getChat, sendMessage } from "../api/backend";

export default function Dashboard() {
  const [chats, setChats] = useState<any[]>([]);
  const [active, setActive] = useState<string>();
  const [msgs, setMsgs] = useState<any[]>([]);

  const refresh = async () => setChats(await getChats());
  const openChat = async (id: string) => {
    setActive(id);
    setMsgs(await getChat(id));
  };
  const onSend = async (text: string) => {
    if (!active) return;
    setMsgs(await sendMessage(active, text));
    refresh();
  };

  useEffect(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t); }, []);

  return (
    <div className="min-h-screen flex flex-col">
      <HeaderBar />
      <div className="flex flex-1">
        <ChatList chats={chats} active={active} onPick={openChat} />
        <div className="flex flex-col flex-1">
          <ChatWindow messages={msgs} />
          <MessageInput disabled={!active} onSend={onSend} />
        </div>
        <ToolsPanel user_id={active} />
      </div>
    </div>
  );
}
