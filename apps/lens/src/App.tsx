import * as React from "react";
import { Chat } from "@/components/Chat";
import { Search } from "@/components/Search";
import { Connectors } from "@/components/Connectors";
import { Access } from "@/components/Access";
import { Datasets } from "@/components/Datasets";
import { Notes } from "@/components/Notes";
import { LocalTools } from "@/components/LocalTools";
import { KnowledgeGraph } from "@/components/graph/KnowledgeGraph";
import { ApprovalPanel } from "@/components/approval/ApprovalPanel";
import { Memory } from "@/components/Memory";
import { CommandPalette } from "@/components/CommandPalette";
import { ResourcePicker } from "@/components/ResourcePicker";
import {
  Shield,
  MessageSquare,
  Search as SearchIcon,
  Share2,
  Network,
  FolderPlus,
  Database,
  BookOpen,
  Terminal,
  GitBranch,
  CheckCircle,
  Brain,
  ArrowLeft,
  X,
  PanelLeft,
  ChevronRight,
  Sun,
  Moon,
} from "lucide-react";

type ActiveTab =
  | "chat"
  | "search"
  | "connectors"
  | "access"
  | "datasets"
  | "notes"
  | "tools"
  | "graph"
  | "approval"
  | "memory";

interface NavItem {
  id: ActiveTab;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  description?: string;
  badge?: string;
}

const SIDEBAR_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Core",
    items: [
      {
        id: "chat",
        label: "AI Chat",
        icon: MessageSquare,
        description: "Sovereign AI assistant & tool execution",
      },
      {
        id: "connectors",
        label: "Connectors",
        icon: Share2,
        description: "Enterprise data source integrations",
      },
    ],
  },
  {
    title: "Knowledge & Study",
    items: [
      {
        id: "datasets",
        label: "Datasets & Files",
        icon: Database,
        description: "Study hub, multi-format PDFs, PPTs, tables",
      },
      {
        id: "notes",
        label: "Notes & Canvas",
        icon: BookOpen,
        description: "Document notes & per-document AI chat",
      },
      {
        id: "search",
        label: "Hybrid Search",
        icon: SearchIcon,
        description: "RRF vector & keyword retrieval",
      },
      {
        id: "graph",
        label: "Knowledge Graph",
        icon: GitBranch,
        description: "Entity relationships & graph query engine",
      },
      {
        id: "memory",
        label: "Memory Recall",
        icon: Brain,
        description: "Episodic conversation & semantic storage",
      },
    ],
  },
  {
    title: "Security & Governance",
    items: [
      {
        id: "approval",
        label: "Approvals",
        icon: CheckCircle,
        description: "Human-in-the-loop action proposals",
      },
      {
        id: "access",
        label: "Access Graph",
        icon: Network,
        description: "Zanzibar relationship-based permissions",
      },
      {
        id: "tools",
        label: "Local Tools",
        icon: Terminal,
        description: "Sandboxed commands & system utilities",
      },
    ],
  },
];

const TAB_LABELS: Record<ActiveTab, string> = {
  chat: "Chat",
  connectors: "Connectors",
  datasets: "Datasets",
  notes: "Notes",
  search: "Search",
  graph: "Knowledge Graph",
  memory: "Memory",
  approval: "Approvals",
  access: "Access Graph",
  tools: "Local Tools",
};

export function App() {
  const [activeTab, setActiveTab] = React.useState<ActiveTab>("chat");
  const [isSidebarOpen, setIsSidebarOpen] = React.useState(false);
  const [currentTenantId] = React.useState("corp-default");
  const [currentUserId] = React.useState("alice");
  const [isCommandOpen, setIsCommandOpen] = React.useState(false);
  const [isResourcePickerOpen, setIsResourcePickerOpen] = React.useState(false);
  const [initialChatQuery, setInitialChatQuery] = React.useState<string | undefined>(undefined);

  // Dark Mode State - Default to light (creamy white)
  const [isDarkMode, setIsDarkMode] = React.useState<boolean>(() => {
    const saved = localStorage.getItem("aegismind-theme");
    if (saved) {
      return saved === "dark";
    }
    return false; // Creamy white default
  });

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("oauth")) {
      setActiveTab("connectors");
    }
  }, []);

  React.useEffect(() => {
    const root = document.documentElement;
    if (isDarkMode) {
      root.classList.add("dark");
      localStorage.setItem("aegismind-theme", "dark");
    } else {
      root.classList.remove("dark");
      localStorage.setItem("aegismind-theme", "light");
    }
  }, [isDarkMode]);

  const toggleTheme = () => setIsDarkMode((prev) => !prev);

  const handleBackToChat = React.useCallback(() => {
    setActiveTab("chat");
  }, []);

  // Keyboard shortcut to toggle sidebar (Cmd+B / Ctrl+B)
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setIsSidebarOpen((prev) => !prev);
      }
      if (e.key === "Escape" && isSidebarOpen) {
        setIsSidebarOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSidebarOpen]);

  const selectTab = (tab: ActiveTab) => {
    setActiveTab(tab);
    setIsSidebarOpen(false);
  };

  return (
    <div className="min-h-screen bg-background ambient-glow text-foreground flex flex-col font-sans selection:bg-primary/20 selection:text-primary transition-colors duration-200">
      {/* ─── SPACIOUS MINIMALIST HEADER ─── */}
      <header className="sticky top-0 z-40 h-16 border-b border-border/80 bg-card/85 backdrop-blur-xl transition-all shadow-xs">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-full flex items-center justify-between gap-4">
          {/* Left: Single Sidebar Trigger + Brand Logo + Minimalist Navigation [Chat, Connectors, Ingest] */}
          <div className="flex items-center gap-3 sm:gap-4">
            {/* The ONE and only Sidebar Toggle for all tools & features */}
            <button
              type="button"
              onClick={() => setIsSidebarOpen(true)}
              aria-label="Open sidebar"
              className="h-9 w-9 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary transition-all border border-border/70 hover:border-border shadow-2xs"
              title="All Features & Tools (Ctrl+B)"
            >
              <PanelLeft className="h-4.5 w-4.5" />
            </button>

            {/* Brand Title */}
            <div
              onClick={() => setActiveTab("chat")}
              className="flex items-center gap-2.5 cursor-pointer group pr-2 select-none"
            >
              <div className="h-8 w-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center text-primary shadow-xs group-hover:border-primary transition-all">
                <Shield className="h-4.5 w-4.5 transition-transform group-hover:scale-105" />
              </div>
              <div className="flex items-center gap-1.5">
                <span className="font-semibold text-base tracking-tight text-foreground">
                  AegisMind
                </span>
                <span className="hidden sm:inline-block text-[10px] font-medium px-1.5 py-0.5 rounded bg-primary/15 text-primary tracking-normal">
                  Lens
                </span>
              </div>
            </div>

            {/* Minimalist Navigation: ONLY Chat, Connectors, Ingest */}
            <nav className="flex items-center gap-1.5 pl-3 sm:pl-4 border-l border-border/70 text-xs">
              {/* 1. Chat */}
              <button
                type="button"
                onClick={() => setActiveTab("chat")}
                className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs sm:text-[13px] font-medium transition-all ${
                  activeTab === "chat"
                    ? "bg-primary text-primary-foreground shadow-sm shadow-primary/25"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                }`}
              >
                <MessageSquare className="h-4 w-4" />
                <span>Chat</span>
              </button>

              {/* 2. Connectors */}
              <button
                type="button"
                onClick={() => setActiveTab("connectors")}
                className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs sm:text-[13px] font-medium transition-all ${
                  activeTab === "connectors"
                    ? "bg-primary text-primary-foreground shadow-sm shadow-primary/25"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                }`}
              >
                <Share2 className="h-4 w-4" />
                <span className="hidden sm:inline">Connectors</span>
              </button>

              {/* 3. Ingest */}
              <button
                type="button"
                onClick={() => setIsResourcePickerOpen(true)}
                className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs sm:text-[13px] font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 transition-all shadow-xs"
                title="Ingest files, documents & data sources"
              >
                <FolderPlus className="h-4 w-4" />
                <span>Ingest</span>
              </button>

              {/* Context Breadcrumb when a sidebar-only feature is active */}
              {activeTab !== "chat" && activeTab !== "connectors" && (
                <div className="hidden md:flex items-center gap-2 ml-2 pl-3 border-l border-border/60 text-xs">
                  <span className="text-muted-foreground text-[11px]">Active:</span>
                  <span className="font-medium text-foreground bg-secondary px-2.5 py-1 rounded-md border border-border/70">
                    {TAB_LABELS[activeTab]}
                  </span>
                  <button
                    type="button"
                    onClick={handleBackToChat}
                    className="flex items-center gap-1 text-xs text-primary hover:underline ml-1 font-medium"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    <span>Back to Chat</span>
                  </button>
                </div>
              )}
            </nav>
          </div>

          {/* Right Header: Dark Mode Toggle only (Clean & Minimalist) */}
          <div className="flex items-center gap-2">
            {/* Dark / Light Mode Toggle Button */}
            <button
              type="button"
              onClick={toggleTheme}
              aria-label="Toggle theme"
              className="h-9 w-9 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary transition-all border border-border/70 hover:border-border shadow-2xs"
              title={isDarkMode ? "Switch to Creamy White light mode" : "Switch to Dark mode"}
            >
              {isDarkMode ? (
                <Sun className="h-4 w-4 text-amber-400 transition-transform hover:rotate-45" />
              ) : (
                <Moon className="h-4 w-4 text-slate-700 transition-transform hover:-rotate-12" />
              )}
            </button>
          </div>
        </div>
      </header>

      {/* ─── SLEEK MODERN SLIDE-OVER SIDEBAR ─── */}
      {isSidebarOpen && (
        <div className="fixed inset-0 z-50 flex">
          {/* Backdrop Blur */}
          <div
            onClick={() => setIsSidebarOpen(false)}
            className="fixed inset-0 bg-black/40 backdrop-blur-sm transition-opacity animate-in fade-in duration-200"
          />

          {/* Drawer Content */}
          <div className="relative w-80 max-w-[85vw] bg-card border-r border-border/80 shadow-2xl flex flex-col h-full z-10 animate-in slide-in-from-left duration-200">
            {/* Drawer Header */}
            <div className="p-4 border-b border-border/70 flex items-center justify-between bg-muted/20">
              <div className="flex items-center gap-2.5">
                <div className="h-8 w-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center text-primary">
                  <Shield className="h-4.5 w-4.5" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold tracking-tight text-foreground">
                    AegisMind Hub
                  </h2>
                  <p className="text-[11px] text-muted-foreground">
                    Sovereign Knowledge & Governance
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsSidebarOpen(false)}
                className="h-8 w-8 rounded-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Quick Action: Ingest Banner */}
            <div className="p-3 border-b border-border/60 bg-primary/5">
              <button
                type="button"
                onClick={() => {
                  setIsSidebarOpen(false);
                  setIsResourcePickerOpen(true);
                }}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg bg-primary/10 hover:bg-primary/20 border border-primary/25 text-xs text-primary font-medium transition-all group"
              >
                <div className="flex items-center gap-2">
                  <FolderPlus className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                  <span>Ingest Documents & Files</span>
                </div>
                <ChevronRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
              </button>
            </div>

            {/* Navigation Sections */}
            <div className="flex-1 overflow-y-auto p-3 space-y-5">
              {SIDEBAR_SECTIONS.map((section) => (
                <div key={section.title} className="space-y-1">
                  <div className="px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {section.title}
                  </div>
                  <div className="space-y-0.5">
                    {section.items.map((item) => {
                      const Icon = item.icon;
                      const isActive = activeTab === item.id;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => selectTab(item.id)}
                          className={`w-full flex items-center gap-3 px-2.5 py-2 rounded-lg text-left text-xs transition-all group ${
                            isActive
                              ? "bg-primary text-primary-foreground font-medium shadow-xs"
                              : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                          }`}
                        >
                          <Icon
                            className={`h-4 w-4 shrink-0 transition-colors ${
                              isActive
                                ? "text-primary-foreground"
                                : "text-primary group-hover:text-primary"
                            }`}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between">
                              <span className="truncate">{item.label}</span>
                              {isActive && (
                                <span className="h-1.5 w-1.5 rounded-full bg-primary-foreground" />
                              )}
                            </div>
                            {item.description && (
                              <p
                                className={`text-[10px] truncate ${
                                  isActive
                                    ? "text-primary-foreground/80"
                                    : "text-muted-foreground/75"
                                }`}
                              >
                                {item.description}
                              </p>
                            )}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            {/* Drawer Footer: System Status */}
            <div className="p-3 border-t border-border/70 bg-card/60 space-y-2 text-xs">
              <div className="flex items-center justify-between px-1 text-[10px] text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  SpiceDB Active
                </span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400">Zero Stale Reads</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─── MAIN CONTENT CANVAS ─── */}
      <main className="flex-1 flex flex-col">
        {/* Chat component is kept mounted to preserve conversation state across tab switching until browser refresh */}
        <div className={activeTab === "chat" ? "flex-1 flex flex-col" : "hidden"}>
          <Chat
            currentTenantId={currentTenantId}
            currentUserId={currentUserId}
            initialQuery={initialChatQuery}
            onClearInitialQuery={() => setInitialChatQuery(undefined)}
          />
        </div>
        {activeTab === "search" && (
          <Search currentTenantId={currentTenantId} currentUserId={currentUserId} />
        )}
        <div className={activeTab === "connectors" ? "flex-1 flex flex-col" : "hidden"}>
          <Connectors />
        </div>
        {activeTab === "access" && (
          <Access currentTenantId={currentTenantId} currentUserId={currentUserId} />
        )}
        {activeTab === "datasets" && (
          <Datasets
            currentTenantId={currentTenantId}
            currentUserId={currentUserId}
            onNavigateToChat={(query) => {
              if (query) {
                setInitialChatQuery(`What are the key points in ${query}?`);
              }
              setActiveTab("chat");
            }}
          />
        )}
        {activeTab === "notes" && (
          <Notes
            currentUserId={currentUserId}
            currentTenantId={currentTenantId}
            onNavigateToChat={(query) => {
              if (query) {
                setInitialChatQuery(query);
              }
              setActiveTab("chat");
            }}
          />
        )}
        {activeTab === "tools" && <LocalTools />}
        {activeTab === "graph" && <KnowledgeGraph />}
        {activeTab === "approval" && <ApprovalPanel />}
        {activeTab === "memory" && (
          <Memory currentUserId={currentUserId} currentTenantId={currentTenantId} />
        )}
      </main>

      {/* ─── MINIMALIST FOOTER STATUS BAR ─── */}
      <footer className="border-t border-border/70 bg-card/40 py-2 px-4 text-[11px] text-muted-foreground">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-1">
          <div className="flex items-center gap-2">
            <span className="flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            <span>AegisMind Core v0.1.0</span>
            <span>•</span>
            <span>Sovereign Local Engine Active</span>
            <span>•</span>
            <span>RRF Vector Active</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setIsSidebarOpen(true)}
              className="text-primary hover:underline flex items-center gap-1 font-medium"
            >
              <span>Explore all tools</span>
              <ChevronRight className="h-3 w-3" />
            </button>
          </div>
        </div>
      </footer>

      {/* Command Palette Modal */}
      <CommandPalette
        open={isCommandOpen}
        onOpenChange={setIsCommandOpen}
        onNavigate={(view) => setActiveTab(view)}
        onOpenResourcePicker={() => setIsResourcePickerOpen(true)}
      />

      {/* Resource Picker Modal */}
      <ResourcePicker
        open={isResourcePickerOpen}
        onOpenChange={setIsResourcePickerOpen}
      />
    </div>
  );
}
