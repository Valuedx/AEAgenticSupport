import { useState, useEffect, useRef } from "react";
import { Send, User, Bot, X, MessageSquare, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useWorkflowStore } from "@/store/workflowStore";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export function ChatPanel({ onClose }: { onClose?: () => void }) {
  const [sessionId] = useState(() => `orch-${Math.random().toString(36).substring(2, 11)}`);
  const [messages, setMessages] = useState<Message[]>([
    { role: "assistant", content: "Hello! I am your Agentic Support assistant. How can I help you today?" }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMsg = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: userMsg }]);
    setLoading(true);

    try {
      const res = await fetch("http://localhost:5050/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg, session_id: sessionId })
      });
      const data = await res.json();
      const reply = data.response || data.reply || data.error || "No response";
      setMessages(prev => [...prev, { role: "assistant", content: reply }]);

      // ── UI Syncing Logic ──
      // 1. Check for explicit metadata from the server (preferred)
      if (data.metadata?.workflow_id && data.metadata?.instance_id) {
        const { workflow_id, instance_id } = data.metadata;
        console.log("Syncing UI via metadata:", workflow_id, instance_id);
        useWorkflowStore.getState().loadInstance(workflow_id, instance_id);
      } 
      // 2. Fallback to regex if metadata is missing (legacy/flexible)
      else {
        const match = reply.match(/\[WF_ID:\s*([a-f0-9-]+)\]\s*\[INSTANCE_ID:\s*([a-f0-9-]+)\]/i);
        if (match) {
          const wfId = match[1];
          const instId = match[2];
          console.log("Syncing UI via regex:", wfId, instId);
          useWorkflowStore.getState().loadInstance(wfId, instId);
        }
      }
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: "Error: Could not connect to agent server (5050). Ensure it is running with 'python agent_server.py'." }]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: "auto" });
    }
  }, [messages]);

  return (
    <div className="flex flex-col h-full bg-sidebar">
      <div className="flex items-center justify-between px-4 py-2 border-b bg-background/50 backdrop-blur">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-primary" />
          <h2 className="text-xs font-semibold">Agent Chat</h2>
        </div>
        <div className="flex items-center gap-1">
          <Button 
            variant="ghost" 
            size="icon" 
            onClick={() => setMessages([{ role: "assistant", content: "Chat cleared. How can I help?" }])} 
            className="h-7 w-7" 
            title="Clear Chat"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
          {onClose && (
            <Button variant="ghost" size="icon" onClick={onClose} className="h-7 w-7">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      <div className="flex-1 p-4 overflow-y-auto min-h-0">
        <div className="space-y-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}>
              <div className={`flex items-center gap-1.5 mb-1 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                {m.role === "user" ? (
                  <div className="h-5 w-5 rounded-full bg-primary/20 flex items-center justify-center">
                    <User className="h-3 w-3 text-primary" />
                  </div>
                ) : (
                  <div className="h-5 w-5 rounded-full bg-secondary flex items-center justify-center">
                    <Bot className="h-3 w-3 text-secondary-foreground" />
                  </div>
                )}
                <span className="text-[10px] font-medium uppercase text-muted-foreground">
                  {m.role === "user" ? "You" : "Agent"}
                </span>
              </div>
              <div className={`max-w-[90%] rounded-lg px-3 py-2 text-sm shadow-sm ${
                m.role === "user" 
                  ? "bg-primary text-primary-foreground rounded-tr-none" 
                  : "bg-background border rounded-tl-none text-foreground"
              }`}>
                {m.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex items-center gap-2 text-muted-foreground italic text-xs">
              <Loader2 className="h-3 w-3 animate-spin" />
              Agent is thinking...
            </div>
          )}
          <div ref={scrollRef} />
        </div>
      </div>

      <div className="p-4 border-t bg-background/50 backdrop-blur">
        <div className="flex gap-2">
          <Input 
            placeholder="Ask anything..." 
            value={input} 
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleSend()}
            disabled={loading}
            className="flex-1"
          />
          <Button size="icon" onClick={handleSend} disabled={loading || !input.trim()}>
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="text-[10px] text-muted-foreground mt-2 text-center">
          Connected to Agentic Support (Port 5050)
        </p>
      </div>
    </div>
  );
}
