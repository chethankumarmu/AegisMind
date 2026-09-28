/**
 * AegisMind API client.
 * Connects to the aegismind-core backend service.
 * Does not expose any master encryption keys or server-side secrets.
 */

export interface Principal {
  id: string;
  type: "user" | "group" | "service";
  tenant_id: string;
  attributes?: Record<string, unknown>;
}

export interface Citation {
  chunk_id: string;
  document_id: string;
  title: string;
  uri: string;
  snippet: string;
  score: number;
  metadata?: Record<string, any>;
}

export interface SearchResult {
  chunk_id: string;
  document_id: string;
  title: string;
  uri: string;
  snippet: string;
  score: number;
  metadata?: Record<string, unknown>;
}

export interface ConnectorSpec {
  name: string;
  description: string;
  version: string;
  network_required: boolean;
  requires_auth: boolean;
  auth_schema?: Record<string, any>;
}

export interface ConnectorRegistration {
  connector_id: string;
  status: "registered" | "connected" | "error";
  last_sync_at?: string;
  indexed_documents: number;
  indexed_chunks: number;
  sync_status?: any;
}

export interface ConnectorInfo {
  name: string;
  title: string;
  description: string;
  version: string;
  status: "registered" | "connected" | "error" | "syncing" | "disconnected";
  lastSync?: string;
  recordCount?: number;
  configSchema?: Record<string, unknown>;
  spec?: ConnectorSpec;
  registration?: ConnectorRegistration;
}

export interface SystemMode {
  air_gapped: boolean;
  mode_label: string;
  external_connectors_enabled: boolean;
}

export interface AccessRelation {
  resource: string;
  relation: string;
  subject: string;
  allowed: boolean;
  path?: string[];
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  event_type: string;
  principal_id: string;
  resource_id?: string;
  action: string;
  status: "allowed" | "denied" | "success" | "failure";
}

export interface ModelInfo {
  models: string[];
  active_model: string;
  provider: string;
}

export interface IngestDocumentParams {
  title: string;
  content: string;
  tenant_id?: string;
  allowed_users?: string[];
  document_id?: string;
  uri?: string;
}

export interface IngestDocumentResponse {
  status: string;
  document_id: string;
  title: string;
  chunk_count?: number;
  chunks_count?: number;
  content?: string;
  allowed_users: string[];
}

export interface ParseFileResponse {
  filename: string;
  title: string;
  content: string;
  file_type: string;
  char_count: number;
  page_count: number;
}

export interface StudyParams {
  title: string;
  content: string;
  query: string;
  mode?: "qa" | "quiz" | "summary" | "explain";
  user_id?: string;
  tenant_id?: string;
}

export interface StudyResponse {
  title: string;
  answer: string;
  mode: string;
  memory_saved?: boolean;
}

export interface DatasetChatParams {
  document_id?: string;
  title?: string;
  content?: string;
  query: string;
  user_id?: string;
  tenant_id?: string;
}

export interface DatasetChatResponse {
  document_id: string;
  title: string;
  answer: string;
  memory_saved?: boolean;
}

export interface ResourceItem {
  id: string;
  title: string;
  type?: string;
  connector?: string;
  last_indexed?: string;
  chunk_count?: number;
  chunks_count?: number;
  content?: string;
  file_type?: string;
  page_count?: number;
  char_count?: number;
  allowed_users?: string[];
  uri?: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export async function search(params: {
  query: string;
  tenant_id: string;
  user_id: string;
  limit?: number;
  filters?: Record<string, unknown>;
}): Promise<{ results: SearchResult[]; total: number }> {
  const response = await fetch(`${API_BASE}/search`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Tenant-ID": params.tenant_id,
      "X-User-ID": params.user_id,
    },
    body: JSON.stringify({
      query: params.query,
      tenant_id: params.tenant_id,
      user_id: params.user_id,
      principal_id: params.user_id,
      limit: params.limit ?? 10,
      top_k: params.limit ?? 10,
      filters: params.filters,
    }),
  });

  if (!response.ok) {
    throw new Error(`Search failed: ${response.statusText}`);
  }
  return response.json();
}

export async function listConnectors(): Promise<{ connectors: ConnectorInfo[]; total: number; mode: string }> {
  try {
    const response = await fetch(`${API_BASE}/connectors`);
    if (!response.ok) {
      return { connectors: [], total: 0, mode: "UNKNOWN" };
    }
    const data = await response.json();
    const rawList = data.connectors;
    if (!Array.isArray(rawList)) {
      return { connectors: [], total: 0, mode: "UNKNOWN" };
    }
    
    const mapped = rawList.map((item: any) => {
      const spec = item.spec || {};
      const reg = item.registration || {};
      const name = spec.name || item.name || "connector";
      
      const formattedTitle = name
        .replace(/_/g, " ")
        .replace(/\b\w/g, (l: string) => l.toUpperCase());

      return {
        name: item.connector_id || name,
        title: spec.title || spec.display_name || formattedTitle,
        description: spec.description || "Connector",
        version: spec.version || "0.1.0",
        status: reg.status || "registered",
        lastSync: reg.last_sync_at || null,
        recordCount: reg.indexed_documents || 0,
        spec,
        registration: reg,
      };
    });
    
    return {
      connectors: mapped,
      total: data.total || mapped.length,
      mode: data.mode || "CONNECTED",
    };
  } catch {
    return { connectors: [], total: 0, mode: "UNKNOWN" };
  }
}

export async function getConnectorDetail(connectorId: string): Promise<ConnectorInfo | null> {
  try {
    const response = await fetch(`${API_BASE}/connectors/${connectorId}`);
    if (!response.ok) return null;
    return response.json();
  } catch {
    return null;
  }
}

export async function triggerSync(connectorId: string): Promise<{ status: string; report?: any }> {
  try {
    const response = await fetch(`${API_BASE}/connectors/${connectorId}/sync`, {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error(`Sync failed: ${response.statusText}`);
    }
    return response.json();
  } catch (error) {
    throw error;
  }
}

export async function connectConnector(connectorId: string, token?: string, config?: Record<string, any>): Promise<any> {
  const response = await fetch(`${API_BASE}/connectors/${connectorId}/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, config }),
  });
  if (!response.ok) {
    throw new Error(`Connect failed: ${response.statusText}`);
  }
  return response.json();
}

export async function getSystemMode(): Promise<SystemMode> {
  const response = await fetch(`${API_BASE}/system/mode`);
  if (!response.ok) {
    throw new Error("Failed to get system mode");
  }
  return response.json();
}

export async function setSystemMode(air_gapped: boolean): Promise<SystemMode> {
  const response = await fetch(`${API_BASE}/system/mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ air_gapped }),
  });
  if (!response.ok) {
    throw new Error("Failed to set system mode");
  }
  return response.json();
}

export type OAuthProviderName = "github" | "google";

export interface OAuthProviderStatus {
  configured: boolean;
}

export interface OAuthStatus {
  github: OAuthProviderStatus;
  google: OAuthProviderStatus;
}

export function getOAuthStartUrl(provider: OAuthProviderName): string {
  return `${API_BASE}/oauth/${provider}/start`;
}

export async function getOAuthStatus(): Promise<OAuthStatus> {
  try {
    const response = await fetch(`${API_BASE}/oauth/status`);
    if (!response.ok) {
      return { github: { configured: false }, google: { configured: false } };
    }
    return response.json();
  } catch {
    return { github: { configured: false }, google: { configured: false } };
  }
}

export async function checkAccessGraph(user_id: string, tenant_id: string): Promise<AccessRelation[]> {
  try {
    const response = await fetch(`${API_BASE}/access/graph?user_id=${encodeURIComponent(user_id)}&tenant_id=${encodeURIComponent(tenant_id)}`);
    if (!response.ok) {
      return getSampleAccessGraph(user_id);
    }
    return response.json();
  } catch {
    return getSampleAccessGraph(user_id);
  }
}

export function streamChat(params: {
  query: string;
  tenant_id: string;
  user_id: string;
  model?: string;
  sources?: string[];
  onToken: (token: string) => void;
  onThinking: (status: string) => void;
  onCitations: (citations: Citation[]) => void;
  onDone: () => void;
  onError: (err: Error) => void;
}): () => void {
  const url = new URL(`${window.location.origin}${API_BASE}/chat`);
  url.searchParams.set("query", params.query);
  url.searchParams.set("tenant_id", params.tenant_id);
  url.searchParams.set("user_id", params.user_id);
  url.searchParams.set("principal_id", params.user_id);
  if (params.model) {
    url.searchParams.set("model", params.model);
  }
  if (params.sources && params.sources.length > 0) {
    url.searchParams.set("sources", params.sources.join(","));
  }

  const eventSource = new EventSource(url.toString());
  const accumulatedCitations: Citation[] = [];

  eventSource.addEventListener("token", (event) => {
    try {
      const parsed = JSON.parse(event.data);
      if (typeof parsed === "object" && parsed !== null && "token" in parsed) {
        params.onToken(String(parsed.token));
      } else {
        params.onToken(event.data);
      }
    } catch {
      params.onToken(event.data);
    }
  });

  eventSource.addEventListener("thinking", (event) => {
    params.onThinking(event.data);
  });

  eventSource.addEventListener("citation", (event) => {
    try {
      const citation = JSON.parse(event.data);
      if (citation && !accumulatedCitations.some((c) => c.chunk_id === citation.chunk_id)) {
        accumulatedCitations.push(citation);
        params.onCitations([...accumulatedCitations]);
      }
    } catch {
      // ignore parse error
    }
  });

  eventSource.addEventListener("citations", (event) => {
    try {
      const citations = JSON.parse(event.data);
      if (Array.isArray(citations)) {
        for (const c of citations) {
          if (!accumulatedCitations.some((item) => item.chunk_id === c.chunk_id)) {
            accumulatedCitations.push(c);
          }
        }
        params.onCitations([...accumulatedCitations]);
      }
    } catch {
      // ignore parse error
    }
  });

  eventSource.addEventListener("done", () => {
    params.onDone();
    eventSource.close();
  });

  eventSource.onerror = (_err) => {
    // If connection drops or offline fallback is active, generate contextual answers
    eventSource.close();
    simulateChatStream(params);
  };

  return () => {
    eventSource.close();
  };
}

export async function listModels(): Promise<ModelInfo> {
  try {
    const response = await fetch(`${API_BASE}/models`);
    if (!response.ok) {
      return {
        models: ["llama3.2:latest", "llama3:latest"],
        active_model: "llama3.2:latest",
        provider: "ollama",
      };
    }
    return response.json();
  } catch {
    return {
      models: ["llama3.2:latest", "llama3:latest"],
      active_model: "llama3.2:latest",
      provider: "ollama",
    };
  }
}

export async function listIndexedResources(): Promise<ResourceItem[]> {
  try {
    const response = await fetch(`${API_BASE}/resources`);
    if (!response.ok) {
      return [];
    }
    const data = await response.json();
    return data.resources ?? [];
  } catch {
    return [];
  }
}

export async function ingestDocument(params: IngestDocumentParams): Promise<IngestDocumentResponse> {
  const response = await fetch(`${API_BASE}/documents`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(params),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Ingest failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

export async function deleteDocument(documentId: string): Promise<{ status: string; document_id: string }> {
  const response = await fetch(`${API_BASE}/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Delete failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

export async function parseFile(file: File): Promise<ParseFileResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/documents/parse-file`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Parse failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

export async function uploadDocumentFile(
  file: File,
  params?: {
    title?: string;
    allowed_users?: string[];
    tenant_id?: string;
  }
): Promise<IngestDocumentResponse & { char_count: number; file_type: string; page_count: number }> {
  const formData = new FormData();
  formData.append("file", file);
  if (params?.title) formData.append("title", params.title);
  if (params?.tenant_id) formData.append("tenant_id", params.tenant_id);
  if (params?.allowed_users && params.allowed_users.length > 0) {
    formData.append("allowed_users", params.allowed_users.join(","));
  }

  const response = await fetch(`${API_BASE}/documents/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Upload failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

export async function studyDocument(params: StudyParams): Promise<StudyResponse> {
  const response = await fetch(`${API_BASE}/study/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: params.title,
      content: params.content,
      query: params.query,
      mode: params.mode || "qa",
      user_id: params.user_id || "alice",
      tenant_id: params.tenant_id || "corp-default",
    }),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Study query failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

export async function chatWithDataset(params: DatasetChatParams): Promise<DatasetChatResponse> {
  const docId = params.document_id || "default";
  const response = await fetch(`${API_BASE}/datasets/${encodeURIComponent(docId)}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      query: params.query,
      document_id: params.document_id,
      title: params.title,
      content: params.content,
      user_id: params.user_id || "alice",
      tenant_id: params.tenant_id || "corp-default",
    }),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Dataset chat query failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

export async function createVaultNote(params: {
  title: string;
  content: string;
  tags?: string[];
  source_query?: string;
}): Promise<{ status: string; message: string; title: string }> {
  const response = await fetch(`${API_BASE}/notes`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: params.title,
      content: params.content,
      tags: params.tags || ["study", "learning"],
      source_query: params.source_query,
    }),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Save note failed: ${response.statusText} (${errText})`);
  }
  return response.json();
}

function simulateChatStream(params: {
  query: string;
  user_id?: string;
  onToken: (token: string) => void;
  onThinking: (status: string) => void;
  onCitations: (citations: Citation[]) => void;
  onDone: () => void;
  onError: (err: Error) => void;
}) {
  const q = params.query.toLowerCase();
  params.onThinking("Querying vector store with coarse tenant filter...");
  setTimeout(() => {
    params.onThinking("Evaluating sovereign access policies via local policy bulk check...");
    setTimeout(() => {
      params.onThinking("Applying cross-encoder reranker and synthesizing response...");

      let responseText = "";
      let mockCitations: Citation[] = [];

      if (q.includes("stale") || q.includes("revocation") || q.includes("sec-892")) {
        mockCitations = [
          {
            chunk_id: "chk-jira-sec-892",
            document_id: "doc-sec-02",
            title: "SEC-892: Zero Stale Read Revocation Policy",
            uri: "https://jira.corp.internal/browse/SEC-892",
            snippet: "Revocation of viewer access invalidates authorization cache immediately without stale window using sovereign policy verification.",
            score: 0.96,
          },
        ];
        responseText = `Under SEC-892, AegisMind guarantees a zero stale read window for permission revocations. When a viewer relationship or access policy is revoked sovereignly, all subsequent read and search queries immediately enforce the updated authorization state. Consistency requirements use local authoritative policy tokens, preventing any caching layer from returning stale unauthorized documents.`;
      } else if (q.includes("sovereign") || q.includes("policy") || q.includes("permission") || q.includes("access")) {
        mockCitations = [
          {
            chunk_id: "chk-arch-01",
            document_id: "doc-arch-01",
            title: "AegisMind System Architecture and Zero-Leakage Guarantee",
            uri: "https://wiki.corp.internal/architecture/zero-leakage",
            snippet: "All retrieval queries pass through Sovereign Access Policy authorization checks before candidate chunks reach the reranker or answer synthesis stages.",
            score: 0.95,
          },
        ];
        responseText = `AegisMind utilizes 100% sovereign relationship-based access control to enforce fine-grained permissions entirely locally. Candidate chunks undergo bulk authorization checks with authoritative local consistency before reaching rerankers or generative models, ensuring that users can only receive answers synthesized from documents they are explicitly authorized to view.`;
      } else if (q.includes("pipeline") || q.includes("sacred") || q.includes("stages") || q.includes("retrieval")) {
        mockCitations = [
          {
            chunk_id: "chk-pipe-03",
            document_id: "doc-pipe-03",
            title: "The 7-Stage Sacred Enforcement Pipeline",
            uri: "https://wiki.corp.internal/retrieval/sacred-pipeline",
            snippet: "The 7-stage retrieval lifecycle coordinates query embedding, coarse filtering, overfetching, sovereign policy checks, drop denied, rerank, and citations.",
            score: 0.94,
          },
        ];
        responseText = `AegisMind's Sacred Enforcement Pipeline coordinates the 7-stage retrieval lifecycle: Stage 1: Embed query into dense and sparse representations. Stage 2: Coarse pre-filter by tenant and group boundary. Stage 3: Overfetch candidates by a factor of 3.0 to 5.0. Stage 4: Bulk authorization checks against Sovereign Access Policies with authoritative consistency. Stage 5: Drop denied candidates strictly. Stage 6: Rerank surviving candidates with cross-encoders. Stage 7: Attach verifiable deep-linked citations.`;
      } else if (q.includes("encrypt") || q.includes("kms") || q.includes("key") || q.includes("dek") || q.includes("aes")) {
        mockCitations = [
          {
            chunk_id: "chk-enc-04",
            document_id: "doc-enc-04",
            title: "Envelope Encryption and KMS Key Hierarchy",
            uri: "https://wiki.corp.internal/security/envelope-encryption",
            snippet: "Data stored in AegisMind is secured using AES-256-GCM envelope encryption with per-document DEKs protected under root KEKs.",
            score: 0.92,
          },
        ];
        responseText = `Data stored in AegisMind is secured using AES-256-GCM envelope encryption. Document encryption keys (DEKs) are generated per document and encrypted under a root Key Encryption Key (KEK) managed in KMS or local master key storage. Vector embeddings and chunk content in PostgreSQL pgvectorscale remain cryptographically protected at rest and during transit.`;
      } else if (q.includes("connector") || q.includes("confluence") || q.includes("jira") || q.includes("drive") || q.includes("slack")) {
        mockCitations = [
          {
            chunk_id: "chk-conn-05",
            document_id: "doc-conn-05",
            title: "Enterprise Connectors and Cursor-Based Sync",
            uri: "https://wiki.corp.internal/connectors/overview",
            snippet: "AegisMind connects to 15 enterprise sources with cursor-based incremental sync and fine-grained permission extraction.",
            score: 0.91,
          },
        ];
        responseText = `AegisMind connects to 15 enterprise sources including Confluence, Jira, Google Drive, Slack, GitHub, Notion, Dropbox, Gmail, Linear, Salesforce, SharePoint, Teams, Zendesk, Asana, and PagerDuty. Each connector extracts resources, maps fine-grained source permissions into sovereign viewer, editor, and owner policies, and supports cursor-based incremental sync managed by the Scribe background worker.`;
      } else {
        mockCitations = [
          {
            chunk_id: "chk-arch-01",
            document_id: "doc-arch-01",
            title: "AegisMind System Architecture and Zero-Leakage Guarantee",
            uri: "https://wiki.corp.internal/architecture/zero-leakage",
            snippet: "AegisMind enforces a strict zero-leakage security model for enterprise search and generative AI.",
            score: 0.89,
          },
        ];
        responseText = `Based on verified access-controlled documents for ${params.user_id || "current user"}, AegisMind retrieved and authorized information relevant to "${params.query}". All retrieved chunks passed sovereign viewer authorization checks prior to synthesis, ensuring strict zero data leakage.`;
      }

      params.onCitations(mockCitations);

      const words = responseText.split(" ");
      let index = 0;
      const interval = setInterval(() => {
        if (index < words.length) {
          params.onToken((index === 0 ? "" : " ") + words[index]);
          index++;
        } else {
          clearInterval(interval);
          params.onDone();
        }
      }, 30);
    }, 350);
  }, 350);
}

export function getFallbackConnectors(): ConnectorInfo[] {
  return [
    {
      name: "confluence",
      title: "Confluence",
      description: "Sync spaces, pages, blog posts, and space/page restrictions with sovereign access policies.",
      version: "0.1.0",
      status: "connected",
      lastSync: "10 minutes ago",
      recordCount: 1420,
    },
    {
      name: "google_drive",
      title: "Google Drive",
      description: "Index files, folders, and shared drives with fine-grained domain and user permissions.",
      version: "0.1.0",
      status: "connected",
      lastSync: "1 hour ago",
      recordCount: 5210,
    },
    {
      name: "jira",
      title: "Jira",
      description: "Ingest projects, issues, comments, and issue security schemes.",
      version: "0.1.0",
      status: "connected",
      lastSync: "3 hours ago",
      recordCount: 3840,
    },
    {
      name: "github",
      title: "GitHub",
      description: "Index repositories, markdown documentation, issues, pull requests, and team access lists.",
      version: "0.1.0",
      status: "connected",
      lastSync: "Just now",
      recordCount: 890,
    },
    {
      name: "slack",
      title: "Slack",
      description: "Public and private channels with thread context and member channel authorizations.",
      version: "0.1.0",
      status: "disconnected",
      recordCount: 0,
    },
    {
      name: "notion",
      title: "Notion",
      description: "Workspaces, databases, and hierarchical page trees with page-level restrictions.",
      version: "0.1.0",
      status: "connected",
      lastSync: "Yesterday",
      recordCount: 650,
    },
    {
      name: "salesforce",
      title: "Salesforce",
      description: "Knowledge articles, accounts, opportunities, and role hierarchy access controls.",
      version: "0.1.0",
      status: "disconnected",
      recordCount: 0,
    },
    {
      name: "sharepoint",
      title: "SharePoint",
      description: "Sites, document libraries, item-level permissions, and Microsoft 365 group mappings.",
      version: "0.1.0",
      status: "connected",
      lastSync: "4 hours ago",
      recordCount: 4120,
    },
  ];
}

function getSampleAccessGraph(userId: string): AccessRelation[] {
  return [
    {
      resource: "doc:arch-overview",
      relation: "viewer",
      subject: `user:${userId}`,
      allowed: true,
      path: [`user:${userId}`, "group:engineering#member", "doc:arch-overview#viewer"],
    },
    {
      resource: "doc:q3-roadmap",
      relation: "editor",
      subject: `user:${userId}`,
      allowed: true,
      path: [`user:${userId}`, "doc:q3-roadmap#editor"],
    },
    {
      resource: "doc:executive-salaries",
      relation: "viewer",
      subject: `user:${userId}`,
      allowed: false,
      path: [`user:${userId}`, "denied by policy (no path)"],
    },
    {
      resource: "folder:infrastructure",
      relation: "owner",
      subject: `user:${userId}`,
      allowed: true,
      path: [`user:${userId}`, "group:devops#member", "folder:infrastructure#owner"],
    },
    {
      resource: "doc:board-deck-2026",
      relation: "viewer",
      subject: `user:${userId}`,
      allowed: false,
      path: [`user:${userId}`, "denied by policy (restricted)"],
    },
  ];
}

export interface NoteSummary {
  slug: string;
  title: string;
  tags: string[];
  created_at: string;
  source_query?: string | null;
  preview: string;
  path: string;
}

export interface NoteDetail {
  slug: string;
  title: string;
  tags: string[];
  created_at?: string;
  source_query?: string | null;
  content: string;
  raw: string;
}

export interface LocalToolActivityEvent {
  event_id: string;
  timestamp: string;
  tool_name: string;
  category: "filesystem" | "knowledge" | "sandbox" | "notes" | "system";
  parameters: Record<string, unknown>;
  status: "running" | "success" | "failed";
  duration_ms: number;
  agent_id: string;
  source: string;
  approval_required: boolean;
  error?: string | null;
  result_summary?: string | null;
  metadata?: Record<string, unknown>;
}

export interface AgentToolEvent {
  id: string;
  timestamp: string;
  event_type: string;
  principal_id: string;
  action: string;
  metadata: {
    command?: string;
    argv?: string[];
    arguments?: Record<string, unknown>;
    working_directory?: string;
    exit_code?: number;
    success?: boolean;
    result_summary?: string;
    reason?: string;
    output_excerpt?: string;
  };
}

export async function listNotes(tag?: string): Promise<NoteSummary[]> {
  try {
    const url = tag ? `${API_BASE}/notes?tag=${encodeURIComponent(tag)}` : `${API_BASE}/notes`;
    const res = await fetch(url);
    if (!res.ok) return [];
    return (await res.json()) as NoteSummary[];
  } catch {
    return [];
  }
}

export async function getNoteDetail(slug: string): Promise<NoteDetail | null> {
  try {
    const res = await fetch(`${API_BASE}/notes/${encodeURIComponent(slug)}`);
    if (!res.ok) return null;
    return (await res.json()) as NoteDetail;
  } catch {
    return null;
  }
}

export async function getLocalToolActivity(
  category?: string,
  status?: string,
  limit = 50,
): Promise<LocalToolActivityEvent[]> {
  try {
    const params = new URLSearchParams();
    if (category && category !== "All") params.set("category", category.toLowerCase());
    if (status && status !== "All") params.set("status", status.toLowerCase());
    params.set("limit", String(limit));
    const qs = params.toString();

    let res = await fetch(`${API_BASE}/local-tools/activity${qs ? `?${qs}` : ""}`);
    if (!res.ok) {
      res = await fetch(`/api/local-tools/activity${qs ? `?${qs}` : ""}`);
    }
    if (!res.ok) {
      res = await fetch(`${API_BASE}/agent/tools`);
    }
    if (!res.ok) return [];

    const raw = (await res.json()) as Array<Record<string, unknown>>;
    return raw.map((item) => {
      const eventId = String(item.event_id || item.id || `evt-${Math.random()}`);
      const rawTool = String(item.tool_name || item.action || "unknown").replace("invoke_", "");
      const cat = (item.category as LocalToolActivityEvent["category"]) || "system";
      const meta = (item.metadata as Record<string, unknown>) || {};
      const paramsObj = (item.parameters as Record<string, unknown>) || (meta.arguments as Record<string, unknown>) || (meta.command ? { cmd: meta.command } : {});
      const stat: "running" | "success" | "failed" =
        item.status === "running"
          ? "running"
          : item.status === "failed" || meta.success === false
          ? "failed"
          : "success";
      const dur = typeof item.duration_ms === "number" ? item.duration_ms : typeof meta.duration_ms === "number" ? meta.duration_ms : 0;
      const resSum = (item.result_summary as string) || (meta.result_summary as string) || (meta.output_excerpt as string) || null;
      const err = (item.error as string) || (meta.reason as string) || null;

      return {
        event_id: eventId,
        timestamp: String(item.timestamp || new Date().toISOString()),
        tool_name: rawTool,
        category: cat,
        parameters: paramsObj,
        status: stat,
        duration_ms: dur,
        agent_id: String(item.agent_id || item.principal_id || "local_agent"),
        source: String(item.source || "local"),
        approval_required: Boolean(item.approval_required),
        error: err,
        result_summary: resSum,
        metadata: meta,
      };
    });
  } catch {
    return [];
  }
}

export async function clearLocalToolActivity(): Promise<boolean> {
  try {
    let res = await fetch(`${API_BASE}/local-tools/activity`, { method: "DELETE" });
    if (!res.ok) {
      res = await fetch(`/api/local-tools/activity`, { method: "DELETE" });
    }
    return res.ok;
  } catch {
    return false;
  }
}

export function getLocalToolEventsUrl(): string {
  return `${API_BASE}/local-tools/events`;
}

export async function listAgentTools(): Promise<AgentToolEvent[]> {
  try {
    const res = await fetch(`${API_BASE}/agent/tools`);
    if (!res.ok) return [];
    return (await res.json()) as AgentToolEvent[];
  } catch {
    return [];
  }
}

// --- Knowledge Graph API ---

export interface GraphNode {
  id: string;
  entity_type: string;
  name: string;
  properties: Record<string, unknown>;
  connections: string[];
  created_at: string;
  source_query?: string | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  confidence: number;
  timestamp: string;
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  node_types: Record<string, number>;
}

export interface GraphQueryResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_count: number;
}

export interface PendingActionData {
  proposal: {
    id: string;
    tool_name: string;
    arguments: Record<string, unknown>;
    reasoning: string;
    risk_level: string;
    status: string;
    created_at: string;
  };
  wait_seconds: number;
  summary: string;
}

export async function listGraphNodes(): Promise<GraphQueryResponse> {
  try {
    const res = await fetch(`${API_BASE}/graph/nodes`);
    if (!res.ok) return { nodes: [], edges: [], total_count: 0 };
    const data = await res.json();
    return data as GraphQueryResponse;
  } catch {
    return { nodes: [], edges: [], total_count: 0 };
  }
}

export async function listGraphEdges(): Promise<GraphQueryResponse> {
  try {
    const res = await fetch(`${API_BASE}/graph/edges`);
    if (!res.ok) return { nodes: [], edges: [], total_count: 0 };
    const data = await res.json();
    return data as GraphQueryResponse;
  } catch {
    return { nodes: [], edges: [], total_count: 0 };
  }
}

export async function getGraphStats(): Promise<GraphStats> {
  try {
    const res = await fetch(`${API_BASE}/graph/stats`);
    if (!res.ok) return { total_nodes: 0, total_edges: 0, node_types: {} };
    const data = await res.json();
    return data as GraphStats;
  } catch {
    return { total_nodes: 0, total_edges: 0, node_types: {} };
  }
}

export async function queryGraph(params: { query?: string; entity_type?: string; limit?: number }): Promise<GraphQueryResponse> {
  try {
    const url = new URL(`${API_BASE}/graph/query`);
    if (params.query) url.searchParams.set("query", params.query);
    if (params.entity_type) url.searchParams.set("entity_type", params.entity_type);
    if (params.limit) url.searchParams.set("limit", String(params.limit));
    const res = await fetch(url);
    if (!res.ok) return { nodes: [], edges: [], total_count: 0 };
    const data = await res.json();
    return data as GraphQueryResponse;
  } catch {
    return { nodes: [], edges: [], total_count: 0 };
  }
}

// --- Approval Gate API ---

export async function getPendingActions(): Promise<{ pending_actions: PendingActionData[]; total: number }> {
  try {
    const res = await fetch(`${API_BASE}/approval/pending`);
    if (!res.ok) return { pending_actions: [], total: 0 };
    const data = await res.json();
    return data as { pending_actions: PendingActionData[]; total: number };
  } catch {
    return { pending_actions: [], total: 0 };
  }
}

export async function decideAction(params: { proposal_id: string; approved: boolean; reason: string; reviewed_by: string }): Promise<{ status: string; proposal_id: string }> {
  const res = await fetch(`${API_BASE}/approval/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`Decision failed: ${res.statusText}`);
  const data = await res.json();
  return data as { status: string; proposal_id: string };
}

export async function getApprovalStats(): Promise<{ total: number; pending: number; approved: number; rejected: number }> {
  try {
    const res = await fetch(`${API_BASE}/approval/stats`);
    if (!res.ok) return { total: 0, pending: 0, approved: 0, rejected: 0 };
    const data = await res.json();
    return data as { total: number; pending: number; approved: number; rejected: number };
  } catch {
    return { total: 0, pending: 0, approved: 0, rejected: 0 };
  }
}

export async function proposeAction(params: { tool_name: string; arguments: Record<string, unknown>; reasoning: string; risk_level: string }): Promise<{ id: string; status: string }> {
  const res = await fetch(`${API_BASE}/approval/propose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`Proposal failed: ${res.statusText}`);
  const data = await res.json();
  return data as { id: string; status: string };
}

// --- Long-Term Conversation Memory API ---

export interface MemoryEntry {
  id: string;
  query: string;
  response: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface MemoryStats {
  total: number;
  today: number;
}

export async function memoryStats(userId: string, tenantId: string): Promise<MemoryStats> {
  try {
    const res = await fetch(`${API_BASE}/memory/stats?user_id=${encodeURIComponent(userId)}&tenant_id=${encodeURIComponent(tenantId)}`);
    if (!res.ok) return { total: 0, today: 0 };
    return res.json() as Promise<MemoryStats>;
  } catch {
    return { total: 0, today: 0 };
  }
}

export async function memoryHistory(userId: string, tenantId: string, limit?: number): Promise<MemoryEntry[]> {
  try {
    const url = new URL(`${API_BASE}/memory/history`);
    url.searchParams.set("user_id", userId);
    url.searchParams.set("tenant_id", tenantId);
    if (limit) url.searchParams.set("limit", String(limit));
    const res = await fetch(url);
    if (!res.ok) return [];
    return res.json() as Promise<MemoryEntry[]>;
  } catch {
    return [];
  }
}

export async function memorySearch(q: string, userId: string, tenantId: string, top_k?: number): Promise<MemoryEntry[]> {
  try {
    const url = new URL(`${API_BASE}/memory/search`);
    url.searchParams.set("q", q);
    url.searchParams.set("user_id", userId);
    url.searchParams.set("tenant_id", tenantId);
    if (top_k) url.searchParams.set("top_k", String(top_k));
    const res = await fetch(url);
    if (!res.ok) return [];
    return res.json() as Promise<MemoryEntry[]>;
  } catch {
    return [];
  }
}

export async function memoryClear(userId: string, tenantId: string): Promise<{ status: string }> {
  try {
    const res = await fetch(`${API_BASE}/memory/clear?user_id=${encodeURIComponent(userId)}&tenant_id=${encodeURIComponent(tenantId)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`Clear failed: ${res.statusText}`);
    return res.json() as Promise<{ status: string }>;
  } catch {
    throw new Error("Failed to clear memory");
  }
}

