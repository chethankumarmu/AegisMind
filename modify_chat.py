import sys
import re

filepath = r'd:\aegisMind\apps\lens\src\components\Chat.tsx'
with open(filepath, 'r') as f:
    content = f.read()

# Add sourceFilter state and icons
icons_import_repl = """import {
  SendHorizontal,
  Bot,
  User,
  Shield,
  FileText,
  Paperclip,
  X,
  StopCircle,
  Link2,
  ExternalLink,
  Cpu,
  Lock,
  MessageSquare,
  CornerDownRight,
  Database,
  Github,
  Mail,
  Check
} from "lucide-react";"""

content = re.sub(r'import \{[^}]+\} from "lucide-react";', icons_import_repl, content)

# Insert the sourceFilter state after recallMode
state_repl = """  const [recallMode, setRecallMode] = React.useState(false);
  const [sourceFilter, setSourceFilter] = React.useState({ local: true, github: true, gmail: true });"""
content = content.replace("  const [recallMode, setRecallMode] = React.useState(false);", state_repl)

# Update the streamChat call to pass sources
stream_chat_call = """
    const activeSources = Object.entries(sourceFilter)
      .filter(([_, active]) => active)
      .map(([key]) => key);

    const cancel = streamChat({
      query: fullQuery,
      tenant_id: currentTenantId,
      user_id: currentUserId,
      model: selectedModel || undefined,
      sources: activeSources,
"""
content = content.replace("""    const cancel = streamChat({
      query: fullQuery,
      tenant_id: currentTenantId,
      user_id: currentUserId,
      model: selectedModel || undefined,""", stream_chat_call)

# Add source selector to the context header
source_selector = """
          <div className="flex items-center gap-2 text-xs">
            {/* Source Selector */}
            <div className="flex items-center gap-1.5 mr-2 border-r border-border/60 pr-2">
              <span className="text-muted-foreground mr-1">Sources:</span>
              <button 
                onClick={() => setSourceFilter(prev => ({...prev, local: !prev.local}))}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-colors ${sourceFilter.local ? 'bg-primary/20 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground opacity-50'}`}
              >
                <Database className="w-3 h-3" /> Local
              </button>
              <button 
                onClick={() => setSourceFilter(prev => ({...prev, github: !prev.github}))}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-colors ${sourceFilter.github ? 'bg-primary/20 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground opacity-50'}`}
              >
                <Github className="w-3 h-3" /> GitHub
              </button>
              <button 
                onClick={() => setSourceFilter(prev => ({...prev, gmail: !prev.gmail}))}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-colors ${sourceFilter.gmail ? 'bg-primary/20 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground opacity-50'}`}
              >
                <Mail className="w-3 h-3" /> Gmail
              </button>
            </div>
            
            <span className="text-muted-foreground">Tenant:</span>
"""
content = content.replace("""
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Tenant:</span>
""", source_selector)

# Update Citation Inspector to show source metadata
citation_inspector_repl = """
        {selectedCitation && (
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div className="rounded-lg bg-card border border-border p-3 shadow-xs">
              <div className="mb-2 flex items-center justify-between border-b border-border/50 pb-2">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                  Source Document
                </span>
                <span className="text-[10px] font-mono text-primary/80 bg-primary/10 px-1.5 py-0.5 rounded">
                  Score: {selectedCitation.score.toFixed(3)}
                </span>
              </div>
              <h4 className="text-sm font-semibold text-foreground break-words leading-snug">
                {selectedCitation.title}
              </h4>
              
              <div className="mt-2 space-y-1.5 text-xs text-muted-foreground bg-muted/30 p-2 rounded border border-border/30">
                <div className="flex items-start gap-1.5">
                  <span className="font-medium shrink-0">URI:</span>
                  <span className="break-all font-mono text-[11px]">{selectedCitation.uri}</span>
                </div>
                
                {/* Dynamically render metadata if available */}
                {selectedCitation.metadata && Object.entries(selectedCitation.metadata).map(([key, value]) => {
                  if (key === "source_type") {
                    let icon = <Database className="w-3 h-3 inline mr-1 text-primary" />;
                    if (value === "github") icon = <Github className="w-3 h-3 inline mr-1 text-primary" />;
                    if (value === "gmail") icon = <Mail className="w-3 h-3 inline mr-1 text-primary" />;
                    return (
                      <div key={key} className="flex items-start gap-1.5">
                        <span className="font-medium shrink-0 capitalize">Source Type:</span>
                        <span className="font-medium text-foreground">{icon}{String(value)}</span>
                      </div>
                    );
                  }
                  
                  // Common metadata fields
                  if (typeof value === "string" || typeof value === "number") {
                    return (
                      <div key={key} className="flex items-start gap-1.5">
                        <span className="font-medium shrink-0 capitalize">{key.replace(/_/g, " ")}:</span>
                        <span className="break-all text-foreground/80">{String(value)}</span>
                      </div>
                    );
                  }
                  return null;
                })}
              </div>

              <div className="mt-4 border-t border-border/50 pt-3">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider block mb-2">
                  Retrieved Chunk
                </span>
                <p className="text-xs text-foreground/90 whitespace-pre-wrap font-mono bg-muted/40 p-2.5 rounded-md border border-border/30 leading-relaxed overflow-x-auto">
                  {selectedCitation.snippet}
                </p>
              </div>
            </div>
          </div>
        )}
"""
# Replace the old citation inspector content
old_citation_inspector = """
        {selectedCitation && (
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div className="rounded-lg bg-card border border-border p-3 shadow-xs">
              <div className="mb-2 flex items-center justify-between border-b border-border/50 pb-2">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                  Source Document
                </span>
                <span className="text-[10px] font-mono text-primary/80 bg-primary/10 px-1.5 py-0.5 rounded">
                  Score: {selectedCitation.score.toFixed(3)}
                </span>
              </div>
              <h4 className="text-sm font-semibold text-foreground break-words leading-snug">
                {selectedCitation.title}
              </h4>
              <p className="mt-1 break-all text-xs text-muted-foreground font-mono bg-muted/30 p-1 rounded">
                {selectedCitation.uri}
              </p>
              <div className="mt-4 border-t border-border/50 pt-3">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider block mb-2">
                  Retrieved Chunk
                </span>
                <p className="text-xs text-foreground/90 whitespace-pre-wrap font-mono bg-muted/40 p-2.5 rounded-md border border-border/30 leading-relaxed overflow-x-auto">
                  {selectedCitation.snippet}
                </p>
              </div>
            </div>
          </div>
        )}
"""
content = content.replace(old_citation_inspector, citation_inspector_repl)


with open(filepath, "w") as f:
    f.write(content)

print("Chat.tsx updated successfully.")
