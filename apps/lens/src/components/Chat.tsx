import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { streamChat, listModels, parseFile, type Citation, type ModelInfo } from "@/lib/api";
import { recordExchange } from "@/lib/conversationStore";
import {
  Bot,
  User,
  Shield,
  FileText,
  Paperclip,
  X,
  ExternalLink,
  Cpu,
  Lock,
  CornerDownRight,
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Database,
  Github,
  Mail,
  Plus,
  Square,
  Send,
} from "lucide-react";
import SpeechRecognition, { useSpeechRecognition } from 'react-speech-recognition';

interface AttachedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  isImage: boolean;
  previewUrl?: string;
  file: File;
}

interface MessageAttachment {
  name: string;
  isImage: boolean;
  previewUrl?: string;
  size: number;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  citations?: Citation[];
  timestamp: string;
  attachments?: MessageAttachment[];
}

interface ChatProps {
  currentTenantId: string;
  currentUserId: string;
  initialQuery?: string;
  onClearInitialQuery?: () => void;
}

function escapeHtml(text: string): string {
  return text
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "")
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "")
    .replace(/\u200b/g, "")
    .replace(/\u200c/g, "")
    .replace(/\u200d/g, "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function Chat({
  currentTenantId,
  currentUserId,
  initialQuery,
  onClearInitialQuery,
}: ChatProps) {
  const [messages, setMessages] = React.useState<Message[]>([
    {
      id: "msg-welcome",
      role: "assistant",
      content:
        "Welcome to AegisMind. Ask any question across your enterprise repositories. All answers are strictly governed by sovereign access control evaluated at retrieval time.",
      timestamp: "Just now",
    },
  ]);
  const [inputQuery, setInputQuery] = React.useState("");
  const [attachedFiles, setAttachedFiles] = React.useState<AttachedFile[]>([]);
  const [modelInfo, setModelInfo] = React.useState<ModelInfo | null>(null);
  const [selectedModel, setSelectedModel] = React.useState<string>("");
  const [isStreaming, setIsStreaming] = React.useState(false);
  const [currentThinking, setCurrentThinking] = React.useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = React.useState<Citation | null>(null);
  // When recallMode is true the user is asking the agent to retrieve answers
  // from past conversation memory across all chatboxes
  const [recallMode, setRecallMode] = React.useState(false);
  const [sourceFilter, setSourceFilter] = React.useState({ local: true, github: true, gmail: true });
  const [autoSpeak, setAutoSpeak] = React.useState(true);
  const [isSpeaking, setIsSpeaking] = React.useState(false);
  const [speakingMsgId, setSpeakingMsgId] = React.useState<string | null>(null);
  const [speechErrorMsg, setSpeechErrorMsg] = React.useState<string | null>(null);

  const {
    transcript,
    listening,
    resetTranscript,
    browserSupportsSpeechRecognition,
    isMicrophoneAvailable
  } = useSpeechRecognition();

  const baselineTextRef = React.useRef("");
  const abortStreamRef = React.useRef<(() => void) | null>(null);
  const messagesEndRef = React.useRef<HTMLDivElement>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    return () => {
      if (listening) {
        SpeechRecognition.stopListening();
      }
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, [listening]);

  // Sync transcript to input box
  React.useEffect(() => {
    if (listening) {
      const newQuery = `${baselineTextRef.current} ${transcript}`.trim();
      setInputQuery(newQuery);
    }
  }, [transcript, listening]);

  const speakMessage = (msgId: string, text: string) => {
    if (!("speechSynthesis" in window)) {
      alert("Text-to-speech is not supported in this browser.");
      return;
    }

    if (isSpeaking && speakingMsgId === msgId) {
      window.speechSynthesis.cancel();
      setIsSpeaking(false);
      setSpeakingMsgId(null);
      return;
    }

    window.speechSynthesis.cancel();
    
    // Short timeout to ensure cancel finishes before speaking starts
    setTimeout(() => {
      const cleanText = text
        .replace(/```[\s\S]*?```/g, " Code block omitted. ")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/[*_#\-]/g, " ")
        .replace(/\s+/g, " ")
        .trim();

      if (!cleanText) return;

      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;

      const voices = window.speechSynthesis.getVoices();
      const preferredVoice =
        voices.find(
          (v) =>
            v.lang.startsWith("en") &&
            (v.name.includes("Natural") ||
              v.name.includes("Google") ||
              v.name.includes("Samantha") ||
              v.name.includes("Alex"))
        ) || voices.find((v) => v.lang.startsWith("en"));
      if (preferredVoice) {
        utterance.voice = preferredVoice;
      }

      utterance.onend = () => {
        setIsSpeaking(false);
        setSpeakingMsgId(null);
      };

      utterance.onerror = () => {
        setIsSpeaking(false);
        setSpeakingMsgId(null);
      };

      setIsSpeaking(true);
      setSpeakingMsgId(msgId);
      window.speechSynthesis.speak(utterance);

      // Workaround for Chrome bug where onend never fires for long utterances
      const resumeInterval = setInterval(() => {
        if (!window.speechSynthesis.speaking) {
          clearInterval(resumeInterval);
        } else {
          window.speechSynthesis.pause();
          window.speechSynthesis.resume();
        }
      }, 10000);

      utterance.addEventListener('end', () => clearInterval(resumeInterval));
      utterance.addEventListener('error', () => clearInterval(resumeInterval));
    }, 50);
  };

  const stopMicrophone = React.useCallback(() => {
    SpeechRecognition.stopListening();
  }, []);

  const toggleListening = () => {
    setSpeechErrorMsg(null);

    if (!browserSupportsSpeechRecognition) {
      setSpeechErrorMsg("Speech recognition is not supported in this browser. Please use Chrome.");
      return;
    }

    if (listening) {
      SpeechRecognition.stopListening();
    } else {
      baselineTextRef.current = inputQuery.trim();
      resetTranscript();
      SpeechRecognition.startListening({ continuous: true });
    }
  };


  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  React.useEffect(() => {
    scrollToBottom();
  }, [messages, currentThinking, attachedFiles]);

  React.useEffect(() => {
    listModels()
      .then((info) => {
        setModelInfo(info);
        if (info.active_model) {
          setSelectedModel(info.active_model);
        }
      })
      .catch(() => {});
  }, []);

  React.useEffect(() => {
    if (initialQuery) {
      setInputQuery(initialQuery);
      onClearInitialQuery?.();
    }
  }, [initialQuery, onClearInitialQuery]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    const newFiles: AttachedFile[] = Array.from(e.target.files).map((f) => {
      const isImg = f.type.startsWith("image/");
      return {
        id: `att-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
        name: f.name,
        size: f.size,
        type: f.type,
        isImage: isImg,
        previewUrl: isImg ? URL.createObjectURL(f) : undefined,
        file: f,
      };
    });
    setAttachedFiles((prev) => [...prev, ...newFiles]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeAttachment = (id: string) => {
    setAttachedFiles((prev) => {
      const target = prev.find((f) => f.id === id);
      if (target?.previewUrl) {
        URL.revokeObjectURL(target.previewUrl);
      }
      return prev.filter((f) => f.id !== id);
    });
  };

  const handleSend = async () => {
    if ((!inputQuery.trim() && attachedFiles.length === 0) || isStreaming) return;

    if (listening) {
      stopMicrophone();
    }

    const userMsgId = `user-${Date.now()}`;
    const assistantMsgId = `asst-${Date.now()}`;
    const rawQuery = inputQuery.trim() || "Please analyze the attached file(s).";

    // In recallMode, prefix the query so the agent searches past conversation memory
    const query = recallMode
      ? `[RECALL FROM MEMORY] ${rawQuery}`
      : rawQuery;
    const currentAttachments = [...attachedFiles];

    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: "user",
        content: recallMode ? `[Memory Recall] ${rawQuery}` : rawQuery,
        timestamp: new Date().toISOString(),
        attachments: currentAttachments.map((f) => ({
          name: f.name,
          isImage: f.isImage,
          previewUrl: f.previewUrl,
          size: f.size,
        })),
      },
      {
        id: assistantMsgId,
        role: "assistant",
        content: "",
        citations: [],
        timestamp: new Date().toISOString(),
      },
    ]);

    setInputQuery("");
    setAttachedFiles([]);
    setRecallMode(false);
    setIsStreaming(true);
    setCurrentThinking("Initializing request...");

    // If documents are attached, parse their text in background to augment LLM context
    let fullQuery = query;
    if (currentAttachments.length > 0) {
      try {
        const textSnippets: string[] = [];
        for (const att of currentAttachments) {
          if (!att.isImage) {
            try {
              setCurrentThinking(`Reading attached document: ${att.name}...`);
              const res = await parseFile(att.file);
              if (res.content) {
                textSnippets.push(`--- Attached File: ${att.name} ---\n${res.content.slice(0, 4000)}`);
              }
            } catch {
              // fallback if parser endpoint has issues
            }
          } else {
            textSnippets.push(`[Attached Image: ${att.name}]`);
          }
        }
        if (textSnippets.length > 0) {
          fullQuery = `${textSnippets.join("\n\n")}\n\nUser Question: ${query}`;
        }
      } catch {
        // proceed with base query
      }
    }

    // Captured response for store recording
    let accumulatedResponse = "";


    const activeSources = Object.entries(sourceFilter)
      .filter(([_, active]) => active)
      .map(([key]) => key);

    const cancel = streamChat({
      query: fullQuery,
      tenant_id: currentTenantId,
      user_id: currentUserId,
      model: selectedModel || undefined,
      sources: activeSources,

      onThinking: (status) => {
        setCurrentThinking(status);
      },
      onToken: (token) => {
        accumulatedResponse += token;
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMsgId
              ? { ...msg, content: msg.content + token }
              : msg
          )
        );
      },
      onCitations: (citations) => {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMsgId ? { ...msg, citations } : msg
          )
        );
      },
      onDone: () => {
        setIsStreaming(false);
        setCurrentThinking(null);
        abortStreamRef.current = null;
        // Persist the completed exchange to the cross-chatbox conversation store
        if (accumulatedResponse.trim()) {
          recordExchange({
            threadId: "home",
            label: "Main Chat",
            source: "home",
            userMessage: {
              id: userMsgId,
              content: recallMode ? `[Memory Recall] ${rawQuery}` : rawQuery,
              timestamp: new Date().toISOString(),
            },
            assistantMessage: {
              id: assistantMsgId,
              content: accumulatedResponse,
              timestamp: new Date().toISOString(),
            },
          });

          // Automatically speak back response if autoSpeak is active
          if (autoSpeak) {
            speakMessage(assistantMsgId, accumulatedResponse);
          }
        }
      },
      onError: (err) => {
        setIsStreaming(false);
        setCurrentThinking(null);
        abortStreamRef.current = null;
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMsgId
              ? {
                  ...msg,
                  content:
                    msg.content ||
                    `Error executing access-controlled search: ${err.message}`,
                }
              : msg
          )
        );
      },
    });

    abortStreamRef.current = cancel;
  };

  const handleStop = () => {
    if (abortStreamRef.current) {
      abortStreamRef.current();
      abortStreamRef.current = null;
    }
    setIsStreaming(false);
    setCurrentThinking(null);
  };

  return (
    <div className="flex h-full flex-col lg:flex-row gap-4 p-4 max-w-7xl mx-auto w-full">
      {/* Main Conversation Column */}
      <div className="flex flex-1 flex-col h-[calc(100vh-8rem)] rounded-xl border border-border/80 bg-card/70 backdrop-blur-sm overflow-hidden shadow-xs">
        {/* Context Header */}
        <div className="flex items-center justify-between border-b border-border/70 px-4 py-3 bg-muted/20">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-emerald-500" />
            <span className="text-xs font-medium text-foreground">
              Sovereign Enforced Session
            </span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <button
              type="button"
              onClick={() => {
                const next = !autoSpeak;
                setAutoSpeak(next);
                if (!next && "speechSynthesis" in window) {
                  window.speechSynthesis.cancel();
                  setIsSpeaking(false);
                  setSpeakingMsgId(null);
                }
              }}
              className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-medium border transition-colors ${
                autoSpeak
                  ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-600 dark:text-emerald-400"
                  : "bg-secondary/60 border-border text-muted-foreground hover:text-foreground"
              }`}
              title="Toggle automatic voice response readback"
            >
              {autoSpeak ? (
                <Volume2 className="h-3.5 w-3.5 text-emerald-500" />
              ) : (
                <VolumeX className="h-3.5 w-3.5 text-muted-foreground" />
              )}
              <span>{autoSpeak ? "Voice Output ON" : "Voice Output OFF"}</span>
            </button>

            <div className="flex items-center gap-1.5 mr-2 border-r border-border/60 pr-2">
              <span className="text-muted-foreground mr-1">Sources:</span>
              <button
                type="button"
                onClick={() => setSourceFilter(prev => ({...prev, local: !prev.local}))}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-colors ${sourceFilter.local ? 'bg-primary/20 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground opacity-50'}`}
              >
                <Database className="w-3 h-3" /> Local
              </button>
              <button
                type="button"
                onClick={() => setSourceFilter(prev => ({...prev, github: !prev.github}))}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-colors ${sourceFilter.github ? 'bg-primary/20 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground opacity-50'}`}
              >
                <Github className="w-3 h-3" /> GitHub
              </button>
              <button
                type="button"
                onClick={() => setSourceFilter(prev => ({...prev, gmail: !prev.gmail}))}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-colors ${sourceFilter.gmail ? 'bg-primary/20 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground opacity-50'}`}
              >
                <Mail className="w-3 h-3" /> Gmail
              </button>
            </div>

            <span className="text-muted-foreground">Tenant:</span>
            <Badge variant="outline" className="font-mono text-[11px]">
              {currentTenantId}
            </Badge>
            <span className="text-muted-foreground ml-2">User:</span>
            <Badge variant="outline" className="font-mono text-[11px] text-primary">
              {currentUserId}
            </Badge>
            <div className="flex items-center gap-1.5 ml-2 border-l border-border/60 pl-2">
              <Cpu className="h-3.5 w-3.5 text-primary" />
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="bg-transparent text-xs font-mono text-foreground focus:outline-none cursor-pointer"
                title="Select Ollama model"
              >
                {modelInfo?.models && modelInfo.models.length > 0 ? (
                  modelInfo.models.map((m) => (
                    <option key={m} value={m} className="bg-card">
                      {m}
                    </option>
                  ))
                ) : (
                  <option value="llama3.2:latest" className="bg-card">
                    llama3.2:latest
                  </option>
                )}
              </select>
            </div>
          </div>
        </div>

        {/* Message Stream */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex gap-3 ${
                msg.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              {msg.role === "assistant" && (
                <div className="flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-lg bg-primary/15 text-primary border border-primary/25">
                  <Bot className="h-4 w-4" />
                </div>
              )}

              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm shadow-sm ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground shadow-primary/20"
                    : "bg-card border border-border/70 text-foreground"
                }`}
              >
                {/* Attached Files & Images in Message Bubble */}
                {msg.attachments && msg.attachments.length > 0 && (
                  <div className="mb-2.5 flex flex-wrap gap-2">
                    {msg.attachments.map((att, idx) => (
                      <div
                        key={idx}
                        className={`flex items-center gap-2 p-1.5 rounded-lg border text-xs ${
                          msg.role === "user"
                            ? "bg-primary-foreground/15 border-primary-foreground/25 text-primary-foreground"
                            : "bg-secondary border-border text-foreground"
                        }`}
                      >
                        <FileText className="h-4 w-4 shrink-0" />
                        <div className="min-w-0 pr-1">
                          <p className="truncate font-medium max-w-[150px] text-[11px]">
                            {att.name}
                          </p>
                          <span className="text-[10px] opacity-75">
                            {(att.size / 1024).toFixed(0)} KB
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Message Body */}
                <div className="whitespace-pre-wrap leading-relaxed">
                  {escapeHtml(msg.content)}
                </div>

                {/* Read Aloud Voice Back Button for Assistant Messages */}
                {msg.role === "assistant" && msg.content && (
                  <div className="mt-2.5 pt-1.5 border-t border-border/50 flex items-center justify-between">
                    <button
                      type="button"
                      onClick={() => speakMessage(msg.id, msg.content)}
                      className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                        speakingMsgId === msg.id
                          ? "bg-rose-500/15 text-rose-600 dark:text-rose-400 font-semibold"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                      }`}
                      title="Read answer back aloud"
                    >
                      {speakingMsgId === msg.id ? (
                        <>
                          <VolumeX className="h-3.5 w-3.5 text-rose-500" />
                          <span>Stop Voice</span>
                        </>
                      ) : (
                        <>
                          <Volume2 className="h-3.5 w-3.5 text-primary" />
                          <span>Voice Back</span>
                        </>
                      )}
                    </button>
                    {speakingMsgId === msg.id && (
                      <span className="text-[10px] text-emerald-500 animate-pulse font-medium">
                        Speaking response...
                      </span>
                    )}
                  </div>
                )}



                <div
                  className={`mt-1 text-[10px] ${
                    msg.role === "user"
                      ? "text-primary-foreground/70 text-right"
                      : "text-muted-foreground text-left"
                  }`}
                >
                  {msg.timestamp}
                </div>
              </div>

              {msg.role === "user" && (
                <div className="flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-lg bg-secondary text-foreground border border-border/60">
                  <User className="h-4 w-4" />
                </div>
              )}
            </div>
          ))}

          {/* Thinking Status Indicator */}
          {currentThinking && (
            <div className="flex gap-3 items-center text-xs text-muted-foreground animate-pulse pl-11">
              <Lock className="h-3.5 w-3.5 text-emerald-500" />
              <span>{currentThinking}</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar with Attached Files & Images Tray */}
        <div className="border-t border-border/70 p-3 bg-card/75">
          {/* Attached Files & Images Preview Tray */}
          {attachedFiles.length > 0 && (
            <div className="mb-2.5 flex flex-wrap gap-2 animate-in fade-in-50 duration-150">
              {attachedFiles.map((att) => (
                <div
                  key={att.id}
                  className="flex items-center gap-2 pl-2 pr-1.5 py-1 rounded-lg bg-secondary border border-border/80 text-xs shadow-2xs group"
                >
                  {att.isImage && att.previewUrl ? (
                    <img
                      src={att.previewUrl}
                      alt={att.name}
                      className="h-6 w-6 rounded object-cover border border-border/60"
                    />
                  ) : (
                    <FileText className="h-4 w-4 text-primary shrink-0" />
                  )}
                  <span className="max-w-[130px] truncate font-medium text-foreground text-[11px]">
                    {att.name}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    ({(att.size / 1024).toFixed(0)} KB)
                  </span>
                  <button
                    type="button"
                    onClick={() => removeAttachment(att.id)}
                    className="h-4.5 w-4.5 rounded-full flex items-center justify-center hover:bg-muted text-muted-foreground hover:text-foreground transition-colors ml-0.5"
                    title="Remove attachment"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Speech Error Warning Banner */}
          {speechErrorMsg && (
            <div className="mb-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 animate-in fade-in-50 duration-150">
              <MicOff className="h-3.5 w-3.5 shrink-0 text-amber-500" />
              <span className="flex-1 font-medium">{speechErrorMsg}</span>
              <button
                type="button"
                onClick={() => setSpeechErrorMsg(null)}
                className="text-amber-400 hover:text-amber-600 ml-1"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* Voice Input Listening Active Banner */}
          {listening && (
            <div className="mb-2 px-3 py-1.5 rounded-lg bg-rose-500/10 border border-rose-500/30 flex items-center gap-2 text-xs text-rose-600 dark:text-rose-400 animate-in fade-in-50 duration-150">
              <Mic className="h-3.5 w-3.5 shrink-0 animate-pulse text-rose-500" />
              <span className="flex-1 font-medium">
                {isMicrophoneAvailable === false
                  ? "Microphone access denied. Please allow permissions."
                  : "Voice input active: Listening... Speak into your microphone."}
              </span>
              <button
                type="button"
                onClick={toggleListening}
                className="text-rose-400 hover:text-rose-600 ml-1"
                title="Stop voice input"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* Memory Recall Mode Banner */}
          {recallMode && (
            <div className="mb-2 px-3 py-1.5 rounded-lg bg-violet-500/10 border border-violet-500/30 flex items-center gap-2 text-xs text-violet-600 dark:text-violet-400 animate-in fade-in-50 duration-150">
              <CornerDownRight className="h-3.5 w-3.5 shrink-0" />
              <span className="flex-1 font-medium">
                Memory Recall Mode: your question will be answered from past conversation history across all chatboxes.
              </span>
              <button
                type="button"
                onClick={() => setRecallMode(false)}
                className="text-violet-400 hover:text-violet-600 ml-1"
                title="Cancel recall mode"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex items-center gap-2"
          >
            {/* Hidden file input */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*,.pdf,.docx,.pptx,.txt,.md,.csv,.xlsx,.json"
              onChange={handleFileSelect}
              className="hidden"
            />

            {/* + / Pin button to add files or images */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="h-10 w-10 shrink-0 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary border border-border/70 hover:border-border transition-all shadow-2xs group relative"
              title="Add files or images (+ / Pin)"
            >
              <Plus className="h-4.5 w-4.5 text-foreground/80 group-hover:scale-110 transition-transform" />
              <Paperclip className="h-2.5 w-2.5 text-primary absolute bottom-1.5 right-1.5 opacity-80" />
            </button>

            {/* Microphone Voice Input Button */}
            <button
              type="button"
              onClick={toggleListening}
              className={`h-10 w-10 shrink-0 rounded-lg flex items-center justify-center border transition-all shadow-2xs relative ${
                listening
                  ? "bg-rose-500/20 border-rose-500/60 text-rose-600 dark:text-rose-400 animate-pulse ring-2 ring-rose-500/30"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary border-border/70 hover:border-border"
              }`}
              title={
                listening
                  ? "Listening... Click to stop voice input"
                  : "Voice input (Click to speak)"
              }
            >
              {listening ? (
                <Mic className="h-4.5 w-4.5 text-rose-500 animate-bounce" />
              ) : (
                <Mic className="h-4.5 w-4.5 text-foreground/80 hover:scale-110 transition-transform" />
              )}
              {listening && (
                <span className="absolute -top-1 -right-1 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-rose-500"></span>
                </span>
              )}
            </button>

            {/* Memory Recall Arrow Button */}
            <button
              type="button"
              onClick={() => setRecallMode((prev) => !prev)}
              className={`h-10 w-10 shrink-0 rounded-lg flex items-center justify-center border transition-all shadow-2xs ${
                recallMode
                  ? "bg-violet-500/20 border-violet-500/50 text-violet-600 dark:text-violet-400"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary border-border/70 hover:border-border"
              }`}
              title="Toggle Memory Recall Mode: ask the AI to answer from past conversation history"
            >
              <CornerDownRight className="h-4 w-4" />
            </button>

            <Input
              placeholder={
                recallMode
                  ? "Ask about past conversations... (Memory Recall active)"
                  : attachedFiles.length > 0
                  ? "Ask anything about the attached files or images..."
                  : "Ask anything (e.g. How does zero stale read revocation work?)..."
              }
              value={inputQuery}
              onChange={(e) => {
                setInputQuery(e.target.value);
                if (listening) {
                  baselineTextRef.current = e.target.value;
                  resetTranscript();
                }
              }}
              disabled={isStreaming}
              className={`flex-1 bg-background/90 ${recallMode ? "border-violet-500/40 focus-visible:ring-violet-500/40" : ""}`}
            />

            {isStreaming ? (
              <Button
                type="button"
                variant="destructive"
                onClick={handleStop}
                className="gap-1.5"
              >
                <Square className="h-4 w-4 fill-current" />
                Stop
              </Button>
            ) : (
              <Button
                type="submit"
                disabled={!inputQuery.trim() && attachedFiles.length === 0}
                className="gap-1.5"
              >
                <Send className="h-4 w-4" />
                Send
              </Button>
            )}
          </form>
        </div>
      </div>

      {/* Interactive Citation Detail Panel */}
      {selectedCitation ? (
        <div className="w-full lg:w-96 rounded-xl border border-border/80 bg-card/60 p-4 backdrop-blur-sm flex flex-col justify-between animate-in slide-in-from-right-4 duration-200 shadow-xs">
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-border/50 pb-2">
              <div className="flex items-center gap-1.5">
                <FileText className="h-4 w-4 text-primary" />
                <span className="text-xs font-semibold text-foreground">
                  Citation Inspector
                </span>
              </div>
              <button
                type="button"
                onClick={() => setSelectedCitation(null)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Close
              </button>
            </div>

            <div>
              <h4 className="text-sm font-semibold text-foreground leading-tight">
                {selectedCitation.title}
              </h4>
              <p className="text-xs text-muted-foreground font-mono mt-0.5">
                ID: {selectedCitation.document_id}
              </p>
            </div>

            <Card className="bg-background/60 border-border/60">
              <CardContent className="p-3 text-xs text-muted-foreground leading-relaxed">
                <span className="font-semibold text-foreground">Extracted Snippet:</span>
                <p className="mt-1 italic">"{selectedCitation.snippet}"</p>
              </CardContent>
            </Card>

            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">Reranker Relevance:</span>
              <Badge variant="success">
                {(selectedCitation.score * 100).toFixed(1)}% match
              </Badge>
            </div>

            <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/20 p-2.5 text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-2">
              <Shield className="h-4 w-4 shrink-0" />
              <span>Sovereign policy check passed</span>
            </div>
          </div>

          <div className="pt-4 border-t border-border/50">
            <Button
              variant="outline"
              size="sm"
              className="w-full gap-1.5 text-xs"
              onClick={() => window.open(selectedCitation.uri, "_blank")}
            >
              Open Resource
              <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      ) : (
        <div className="hidden lg:flex w-80 rounded-xl border border-dashed border-border/70 p-6 flex-col items-center justify-center text-center text-muted-foreground bg-card/30">
          <FileText className="h-8 w-8 mb-2 opacity-40" />
          <p className="text-xs font-medium">Citation Preview</p>
          <p className="text-[11px] text-muted-foreground/80 mt-1">
            Click any verified citation badge in the answer to view its source snippet and security token status.
          </p>
        </div>
      )}
    </div>
  );
}
