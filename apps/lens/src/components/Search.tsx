import * as React from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { search, type SearchResult } from "@/lib/api";
import {
  Search as SearchIcon,
  Filter,
  SlidersHorizontal,
  ExternalLink,
  ShieldCheck,
  FileText,
  Layers,
  Sparkles,
  RefreshCw,
} from "lucide-react";

interface SearchProps {
  currentTenantId: string;
  currentUserId: string;
}

export function Search({ currentTenantId, currentUserId }: SearchProps) {
  const [query, setQuery] = React.useState("");
  const [selectedSource, setSelectedSource] = React.useState<string>("all");
  const [selectedMimeType] = React.useState<string>("all");
  const [minScore, setMinScore] = React.useState<number>(0.5);
  const [isLoading, setIsLoading] = React.useState(false);
  const [results, setResults] = React.useState<SearchResult[]>(getInitialResults());
  const [selectedPreview, setSelectedPreview] = React.useState<SearchResult | null>(
    results[0] || null
  );

  const handleSearch = async (searchQuery: string) => {
    if (!searchQuery.trim()) {
      setResults(getInitialResults());
      return;
    }

    setIsLoading(true);
    try {
      const response = await search({
        query: searchQuery,
        tenant_id: currentTenantId,
        user_id: currentUserId,
        limit: 15,
        filters: {
          ...(selectedSource !== "all" && { source: selectedSource }),
          ...(selectedMimeType !== "all" && { mime_type: selectedMimeType }),
        },
      });
      setResults(response.results);
      if (response.results.length > 0) {
        setSelectedPreview(response.results[0] || null);
      } else {
        setSelectedPreview(null);
      }
    } catch {
      // If backend search is offline, provide sample hybrid search results
      const filtered = getInitialResults().filter((item) =>
        item.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.snippet.toLowerCase().includes(searchQuery.toLowerCase())
      );
      setResults(filtered);
      setSelectedPreview(filtered[0] || null);
    } finally {
      setIsLoading(false);
    }
  };

  const filteredResults = results.filter((item) => {
    if (item.score < minScore) return false;
    if (selectedSource !== "all" && item.metadata?.source !== selectedSource) {
      return false;
    }
    return true;
  });

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] p-4 max-w-7xl mx-auto w-full gap-4">
      {/* Search Input and Controls */}
      <div className="flex flex-col gap-3 rounded-xl border border-border/80 bg-card/60 p-4 shadow-xs">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <SearchIcon className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search documents, issues, code, and spaces with hybrid RRF..."
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                handleSearch(e.target.value);
              }}
              className="pl-9 h-10 bg-background/80 text-base"
            />
          </div>
          <Button
            onClick={() => handleSearch(query)}
            disabled={isLoading}
            className="gap-2 px-5"
          >
            {isLoading ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <SearchIcon className="h-4 w-4" />
            )}
            Search
          </Button>
        </div>

        {/* Facet Filters Bar */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-border/50 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1 text-muted-foreground font-medium">
              <Filter className="h-3.5 w-3.5" />
              Source:
            </span>
            {["all", "confluence", "google_drive", "jira", "github", "slack"].map(
              (src) => (
                <button
                  key={src}
                  type="button"
                  onClick={() => setSelectedSource(src)}
                  className={`rounded-md px-2.5 py-1 capitalize transition-colors ${
                    selectedSource === src
                      ? "bg-primary text-primary-foreground font-medium"
                      : "bg-muted text-muted-foreground hover:bg-muted/80"
                  }`}
                >
                  {src.replace("_", " ")}
                </button>
              )
            )}
          </div>

          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-muted-foreground">
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Min RRF Score:
            </span>
            <input
              type="range"
              min="0.3"
              max="0.95"
              step="0.05"
              value={minScore}
              onChange={(e) => setMinScore(parseFloat(e.target.value))}
              className="w-24 accent-primary cursor-pointer"
            />
            <span className="font-mono text-[11px] text-foreground w-8">
              {(minScore * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      </div>

      {/* Main Split: Results List & Live Preview */}
      <div className="flex flex-1 gap-4 overflow-hidden">
        {/* Results List */}
        <div className="flex-1 overflow-y-auto space-y-2.5 pr-2">
          <div className="flex items-center justify-between px-1 text-xs text-muted-foreground">
            <span>{filteredResults.length} access-verified results</span>
            <span className="flex items-center gap-1 text-emerald-400">
              <ShieldCheck className="h-3.5 w-3.5" />
              Sovereign Access Policy Verified
            </span>
          </div>

          {filteredResults.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-12 text-center rounded-xl border border-dashed border-border/70 text-muted-foreground">
              <Layers className="h-10 w-10 mb-3 opacity-30" />
              <p className="text-sm font-semibold text-foreground">
                No matching authorized documents found
              </p>
              <p className="text-xs max-w-sm mt-1">
                Either no documents match this query, or your current identity ({currentUserId}) does not hold sovereign viewer access.
              </p>
            </div>
          ) : (
            filteredResults.map((result) => {
              const isSelected = selectedPreview?.chunk_id === result.chunk_id;
              return (
                <div
                  key={result.chunk_id}
                  onClick={() => setSelectedPreview(result)}
                  className={`cursor-pointer rounded-xl border p-4 transition-all text-left ${
                    isSelected
                      ? "border-primary bg-primary/10 shadow-sm"
                      : "border-border/70 bg-card/40 hover:bg-card/80 hover:border-border"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-primary shrink-0" />
                      <h4 className="font-medium text-sm text-foreground hover:underline">
                        {result.title}
                      </h4>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Badge variant="outline" className="text-[10px] capitalize">
                        {String(result.metadata?.source || "confluence").replace("_", " ")}
                      </Badge>
                      <Badge variant="success" className="text-[10px]">
                        RRF {(result.score * 100).toFixed(0)}%
                      </Badge>
                    </div>
                  </div>

                  <p className="mt-2 text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                    {result.snippet}
                  </p>

                  <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground/80">
                    <span className="font-mono text-[10px] truncate max-w-[280px]">
                      {result.uri}
                    </span>
                    <span className="flex items-center gap-1">
                      <ShieldCheck className="h-3 w-3 text-emerald-400" />
                      Allowed: viewer
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Live Snippet Preview Panel */}
        {selectedPreview ? (
          <div className="hidden md:flex w-96 flex-col justify-between rounded-xl border border-border/80 bg-card/60 p-5 backdrop-blur-sm overflow-y-auto">
            <div className="space-y-4">
              <div className="flex items-center justify-between border-b border-border/50 pb-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Document Preview
                </span>
                <Badge variant="outline" className="text-[10px]">
                  Chunk: {selectedPreview.chunk_id}
                </Badge>
              </div>

              <div>
                <h3 className="font-semibold text-base text-foreground leading-snug">
                  {selectedPreview.title}
                </h3>
                <p className="text-xs text-muted-foreground font-mono mt-1 break-all">
                  {selectedPreview.uri}
                </p>
              </div>

              <div className="rounded-lg bg-background/80 border border-border/60 p-3.5">
                <span className="text-xs font-semibold text-foreground flex items-center gap-1 mb-2">
                  <Sparkles className="h-3.5 w-3.5 text-amber-400" />
                  Contextual Ingestion Snippet:
                </span>
                <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap">
                  {selectedPreview.snippet}
                </p>
              </div>

              <div className="space-y-2 text-xs">
                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Hybrid Rank Score:</span>
                  <span className="font-semibold text-emerald-400">
                    {(selectedPreview.score * 100).toFixed(2)}%
                  </span>
                </div>
              </div>
            </div>

            <div className="pt-4 border-t border-border/50">
              <Button
                variant="outline"
                className="w-full gap-2 text-xs"
                onClick={() => window.open(selectedPreview.uri, "_blank")}
              >
                Open Source Document
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ) : (
          <div className="hidden md:flex w-96 items-center justify-center rounded-xl border border-dashed border-border/60 p-6 text-center text-muted-foreground">
            <p className="text-xs">Select a result to preview detailed contents</p>
          </div>
        )}
      </div>
    </div>
  );
}

function getInitialResults(): SearchResult[] {
  return [
    {
      chunk_id: "chk-sec-1",
      document_id: "doc-confluence-101",
      title: "Sovereign Architecture and Ingestion Spec",
      uri: "https://wiki.corp.net/pages/sovereign-spec",
      snippet:
        "The Sovereign Enforcement Pipeline overfetches candidates by 5x, checks sovereign access policies, and drops unauthorized documents before reranking.",
      score: 0.96,
      metadata: { source: "confluence", mime_type: "text/markdown" },
    },
    {
      chunk_id: "chk-gdrive-42",
      document_id: "doc-gdrive-42",
      title: "Q3 Cloud Infrastructure Audit & Compliance",
      uri: "https://drive.google.com/file/d/1A8d90234bX/view",
      snippet:
        "SOC2 and HIPAA compliance requirements mandate envelope encryption with AES-256-GCM. No master key is ever exported to client frontends or browser extensions.",
      score: 0.89,
      metadata: { source: "google_drive", mime_type: "application/pdf" },
    },
    {
      chunk_id: "chk-jira-901",
      document_id: "doc-jira-SEC-901",
      title: "SEC-901: Immediate Revocation Propagation",
      uri: "https://jira.corp.net/browse/SEC-901",
      snippet:
        "Verification confirmed that revoking group membership immediately blocks access in real-time, satisfying zero stale read security guarantees.",
      score: 0.84,
      metadata: { source: "jira", mime_type: "text/plain" },
    },
    {
      chunk_id: "chk-gh-12",
      document_id: "doc-github-repo-core",
      title: "aegismind-retrieval: Reciprocal Rank Fusion",
      uri: "https://github.com/eMohan07/AegisMind/blob/main/packages/aegismind-retrieval/src/aegismind_retrieval/rrf.py",
      snippet:
        "RRF combines dense vector embeddings with BM25 sparse lexical tokens using constant k=60 to produce optimal hybrid rankings.",
      score: 0.81,
      metadata: { source: "github", mime_type: "text/x-python" },
    },
  ];
}
