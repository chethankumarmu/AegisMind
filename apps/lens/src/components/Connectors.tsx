import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  listConnectors,
  triggerSync,
  getSystemMode,
  setSystemMode,
  connectConnector,
  getOAuthStartUrl,
  getOAuthStatus,
  listIndexedResources,
  uploadDocumentFile,
  deleteDocument,
  ingestDocument,
  type ConnectorInfo,
  type SystemMode,
  type OAuthStatus,
  type ResourceItem,
} from "@/lib/api";
import {
  RefreshCw,
  Settings,
  CheckCircle2,
  Shield,
  KeyRound,
  Search,
  Power,
  Lock,
  Globe,
  Database,
  FileText,
  Github,
  Mail,
  FolderOpen,
  UploadCloud,
  Trash2,
  AlertCircle,
} from "lucide-react";

const getIconForConnector = (name: string) => {
  if (name.includes("github")) return <Github className="h-5 w-5" />;
  if (name.includes("gmail")) return <Mail className="h-5 w-5" />;
  if (name.includes("local")) return <FolderOpen className="h-5 w-5" />;
  if (name.includes("sqlite") || name.includes("sql")) return <Database className="h-5 w-5" />;
  return <FileText className="h-5 w-5" />;
};

function catalogMatch(name: string, airGapped: boolean): boolean {
  const n = name.toLowerCase();
  if (airGapped) {
    return n.includes("local");
  }
  return n.includes("github") || n.includes("gmail");
}

export function Connectors() {
  const [connectors, setConnectors] = React.useState<ConnectorInfo[]>([]);
  const [systemMode, setSystemModeState] = React.useState<SystemMode | null>(null);
  const [oauthStatus, setOauthStatus] = React.useState<OAuthStatus>({
    github: { configured: false },
    google: { configured: false },
  });
  const [searchQuery, setSearchQuery] = React.useState("");
  const [selectedConnector, setSelectedConnector] = React.useState<ConnectorInfo | null>(null);
  const [isConfigOpen, setIsConfigOpen] = React.useState(false);
  const [isSyncing, setIsSyncing] = React.useState<Record<string, boolean>>({});
  const [actionNotice, setActionNotice] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [modeToggleLoading, setModeToggleLoading] = React.useState(false);

  const [configToken, setConfigToken] = React.useState("");
  const [configExtra, setConfigExtra] = React.useState("");

  const [watchPath, setWatchPath] = React.useState("./storage/docs");
  const [datasetTitle, setDatasetTitle] = React.useState("");
  const [datasetNote, setDatasetNote] = React.useState("");
  const [datasets, setDatasets] = React.useState<ResourceItem[]>([]);
  const [isSavingDataset, setIsSavingDataset] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const loadData = React.useCallback(async () => {
    try {
      const [modeRes, listRes, oauthRes] = await Promise.all([
        getSystemMode(),
        listConnectors(),
        getOAuthStatus(),
      ]);
      setSystemModeState(modeRes);
      setConnectors(listRes.connectors);
      setOauthStatus(oauthRes);
      if (modeRes.air_gapped) {
        const resources = await listIndexedResources();
        setDatasets(resources);
      }
    } catch (e) {
      console.error("Failed to load connectors data", e);
    }
  }, []);

  React.useEffect(() => {
    loadData();
  }, [loadData]);

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauth = params.get("oauth");
    const provider = params.get("provider");
    const detail = params.get("detail");
    if (!oauth) return;
    if (oauth === "success") {
      setActionNotice(
        `Connected ${provider === "google" ? "Gmail" : "GitHub"} with your account. You can sync now.`
      );
    } else {
      setActionError(
        `OAuth did not complete${provider ? ` for ${provider}` : ""}${detail ? `: ${detail}` : ""}.`
      );
    }
    loadData();
    params.delete("oauth");
    params.delete("provider");
    params.delete("detail");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}`);
  }, [loadData]);

  const handleModeSelect = async (airGapped: boolean) => {
    if (systemMode && systemMode.air_gapped === airGapped) return;
    setModeToggleLoading(true);
    try {
      const newMode = await setSystemMode(airGapped);
      setSystemModeState(newMode);
      setActionNotice(`System switched to ${newMode.mode_label}`);
      setActionError(null);
      setTimeout(() => setActionNotice(null), 3000);
      const listRes = await listConnectors();
      setConnectors(listRes.connectors);
      if (airGapped) {
        setDatasets(await listIndexedResources());
      }
    } catch (e) {
      console.error(e);
      setActionError("Failed to toggle mode");
    } finally {
      setModeToggleLoading(false);
    }
  };

  const handleSync = async (connector: ConnectorInfo) => {
    setIsSyncing((prev) => ({ ...prev, [connector.name]: true }));
    setActionNotice(null);
    setActionError(null);
    try {
      await triggerSync(connector.name);
      setActionNotice(`Sync started for ${connector.title}`);
      setTimeout(() => setActionNotice(null), 3000);
      setTimeout(loadData, 2000);
    } catch (e) {
      setActionError(`Sync failed for ${connector.title}`);
    } finally {
      setIsSyncing((prev) => ({ ...prev, [connector.name]: false }));
    }
  };

  const openConfig = (connector: ConnectorInfo) => {
    setSelectedConnector(connector);
    setConfigToken("");
    setConfigExtra("");
    setIsConfigOpen(true);
  };

  const startOAuth = (provider: "github" | "google") => {
    const configured = provider === "github" ? oauthStatus.github.configured : oauthStatus.google.configured;
    if (!configured) {
      setActionError(
        provider === "github"
          ? "GitHub OAuth is not configured. Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET, or paste a personal access token in Configure."
          : "Gmail OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET."
      );
      return;
    }
    window.location.href = getOAuthStartUrl(provider);
  };

  const handleSaveConfig = async () => {
    if (!selectedConnector) return;
    try {
      const name = selectedConnector.name.toLowerCase();
      const config: Record<string, unknown> = {};
      if (configExtra) {
        if (name.includes("github")) {
          config.repositories = configExtra;
        } else if (name.includes("gmail")) {
          config.label_filter = configExtra;
        } else if (name.includes("local")) {
          config.watch_paths = configExtra;
        } else {
          config.extra = configExtra;
        }
      }
      await connectConnector(selectedConnector.name, configToken || undefined, config);
      setActionNotice(`Connected ${selectedConnector.title} successfully.`);
      setActionError(null);
      setIsConfigOpen(false);
      loadData();
      setTimeout(() => setActionNotice(null), 3000);
    } catch (e) {
      setActionError(`Failed to connect ${selectedConnector.title}.`);
    }
  };

  const handleConnectLocal = async () => {
    const local = connectors.find((c) => c.name.toLowerCase().includes("local"));
    if (!local) {
      setActionError("Local filesystem connector is not registered.");
      return;
    }
    try {
      await connectConnector(local.name, undefined, { watch_paths: watchPath });
      setActionNotice(`Local filesystem connected at ${watchPath}`);
      setActionError(null);
      loadData();
    } catch (e) {
      setActionError("Failed to connect local filesystem.");
    }
  };

  const handleSaveDatasetFiles = async (files: FileList | null) => {
    const fileList = files ? Array.from(files) : [];
    const baseTitle = datasetTitle.trim();
    const notes = datasetNote.trim();
    if (fileList.length === 0 && !notes) {
      setActionError("Pick local files or paste notes before saving a dataset.");
      return;
    }
    setIsSavingDataset(true);
    setActionError(null);
    try {
      for (const file of fileList) {
        const name = file.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ");
        const title = baseTitle
          ? fileList.length === 1
            ? baseTitle
            : `${baseTitle}: ${name}`
          : name.charAt(0).toUpperCase() + name.slice(1);
        await uploadDocumentFile(file, {
          title,
          tenant_id: "corp-default",
          allowed_users: ["alice"],
        });
      }
      if (notes) {
        await ingestDocument({
          title: baseTitle || "Dataset notes",
          content: notes,
          tenant_id: "corp-default",
          allowed_users: ["alice"],
        });
      }
      setDatasetTitle("");
      setDatasetNote("");
      setDatasets(await listIndexedResources());
      setActionNotice("Dataset saved.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Dataset save failed";
      setActionError(msg);
    } finally {
      setIsSavingDataset(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDeleteDataset = async (id: string, title: string) => {
    try {
      await deleteDocument(id);
      setDatasets((prev) => prev.filter((item) => item.id !== id));
      setActionNotice(`Removed dataset "${title}"`);
    } catch (e) {
      setActionError("Failed to delete dataset.");
    }
  };

  const airGapped = Boolean(systemMode?.air_gapped);
  const filtered = (connectors || []).filter((c) => {
    if (!c) return false;
    if (!catalogMatch(c.name || "", airGapped)) return false;
    const title = c.title || c.name || "";
    const description = c.description || "";
    const q = (searchQuery || "").toLowerCase();
    return title.toLowerCase().includes(q) || description.toLowerCase().includes(q);
  });

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] p-4 max-w-7xl mx-auto w-full gap-4">
      <div className="flex flex-col gap-4 rounded-xl border border-border/80 bg-card/60 p-4">
        <div className="flex items-center justify-between border-b border-border/50 pb-4">
          <div>
            <h2 className="text-xl font-bold text-foreground flex items-center gap-2">
              <Shield className="h-6 w-6 text-primary" />
              Sovereign Connector Marketplace
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              Connect local datasets in Sovereign mode, or GitHub and Gmail in Connected mode.
            </p>
          </div>

          <div className="flex items-center gap-2 bg-muted/50 p-1.5 rounded-lg border border-border">
            <button
              type="button"
              disabled={modeToggleLoading}
              onClick={() => handleModeSelect(true)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                airGapped
                  ? "bg-background text-foreground shadow-sm border border-border"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Lock className="h-3.5 w-3.5 text-orange-500" />
              Sovereign
            </button>
            <button
              type="button"
              disabled={modeToggleLoading}
              onClick={() => handleModeSelect(false)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                !airGapped
                  ? "bg-background text-foreground shadow-sm border border-border"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Globe className="h-3.5 w-3.5 text-green-500" />
              Connected
            </button>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-2">
          <div className="relative w-full sm:w-72">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search connectors..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9 text-xs bg-background/50"
            />
          </div>
          <p className="text-xs text-muted-foreground">
            {airGapped
              ? "Sovereign: local filesystem and personal datasets only."
              : "Connected: sign in with your GitHub or Gmail account."}
          </p>
        </div>
      </div>

      {actionNotice && (
        <div className="rounded-lg bg-primary/10 border border-primary/30 p-3 text-xs text-primary flex items-center gap-2 animate-in fade-in-0">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          <span>{actionNotice}</span>
        </div>
      )}
      {actionError && (
        <div className="rounded-lg bg-destructive/10 border border-destructive/30 p-3 text-xs text-destructive flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{actionError}</span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 overflow-y-auto pr-1 pb-4">
        {filtered.map((connector) => {
          const syncing = isSyncing[connector.name];
          const isConnected = connector.status === "connected";
          const name = connector.name.toLowerCase();
          const isGithub = name.includes("github");
          const isGmail = name.includes("gmail");
          const isLocal = name.includes("local");

          return (
            <Card
              key={connector.name}
              className="flex flex-col justify-between border-border/70 bg-card/40 hover:bg-card/70 hover:border-primary/30 transition-all"
            >
              <CardHeader className="p-4 pb-2">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <div className="p-2 bg-primary/10 rounded-md text-primary">
                      {getIconForConnector(connector.name)}
                    </div>
                    <CardTitle className="text-base font-semibold text-foreground">
                      {connector.title}
                    </CardTitle>
                  </div>
                  <Badge
                    variant={
                      connector.status === "connected"
                        ? "success"
                        : connector.status === "syncing"
                        ? "warning"
                        : connector.status === "error"
                        ? "destructive"
                        : "secondary"
                    }
                    className="capitalize text-[10px]"
                  >
                    {connector.status}
                  </Badge>
                </div>
                <CardDescription className="text-xs text-muted-foreground mt-2 line-clamp-2 h-8">
                  {connector.description}
                </CardDescription>
              </CardHeader>

              <CardContent className="p-4 pt-1 pb-3 text-xs space-y-2">
                <div className="flex justify-between items-center text-muted-foreground bg-muted/20 p-2 rounded">
                  <span className="flex items-center gap-1"><FileText className="w-3 h-3" /> Indexed Docs</span>
                  <span className="font-medium text-foreground">{connector.recordCount?.toLocaleString() || 0}</span>
                </div>
                <div className="flex justify-between items-center text-muted-foreground bg-muted/20 p-2 rounded">
                  <span className="flex items-center gap-1"><RefreshCw className="w-3 h-3" /> Last Sync</span>
                  <span className="font-medium text-foreground">
                    {connector.lastSync ? new Date(connector.lastSync).toLocaleString() : "Never"}
                  </span>
                </div>
                {isGithub && (
                  <p className="text-[10px] text-muted-foreground">
                    OAuth {oauthStatus.github.configured ? "ready" : "needs client ID in .env"}
                  </p>
                )}
                {isGmail && (
                  <p className="text-[10px] text-muted-foreground">
                    OAuth {oauthStatus.google.configured ? "ready" : "needs client ID in .env"}
                  </p>
                )}
              </CardContent>

              <CardFooter className="p-4 pt-2 border-t border-border/30 flex flex-col gap-2">
                {isLocal ? (
                  <>
                    <Input
                      value={watchPath}
                      onChange={(e) => setWatchPath(e.target.value)}
                      placeholder="Watch path on this PC"
                      className="h-8 text-xs"
                    />
                    <div className="flex gap-2 w-full">
                      <Button className="w-full text-xs h-8" onClick={handleConnectLocal}>
                        <Power className="mr-2 h-3 w-3" /> Connect path
                      </Button>
                      <Button
                        variant="default"
                        className="w-full text-xs h-8"
                        onClick={() => handleSync(connector)}
                        disabled={syncing}
                      >
                        <RefreshCw className={`mr-2 h-3 w-3 ${syncing ? "animate-spin" : ""}`} />
                        {syncing ? "Syncing..." : "Sync"}
                      </Button>
                    </div>
                  </>
                ) : !isConnected ? (
                  <div className="flex gap-2 w-full">
                    <Button
                      className="w-full text-xs h-8 bg-primary/90 hover:bg-primary"
                      onClick={() => startOAuth(isGmail ? "google" : "github")}
                    >
                      <Power className="mr-2 h-3 w-3" />
                      {isGmail ? "Connect with Google" : "Connect with GitHub"}
                    </Button>
                    {isGithub && (
                      <Button variant="outline" className="text-xs h-8" onClick={() => openConfig(connector)}>
                        PAT
                      </Button>
                    )}
                  </div>
                ) : (
                  <>
                    <Button
                      variant="outline"
                      className="w-full text-xs h-8 border-border hover:bg-muted"
                      onClick={() => openConfig(connector)}
                    >
                      <Settings className="mr-2 h-3 w-3 text-muted-foreground" /> Configure
                    </Button>
                    <Button
                      variant="default"
                      className="w-full text-xs h-8"
                      onClick={() => handleSync(connector)}
                      disabled={syncing}
                    >
                      <RefreshCw className={`mr-2 h-3 w-3 ${syncing ? "animate-spin" : ""}`} />
                      {syncing ? "Syncing..." : "Sync"}
                    </Button>
                    <Button
                      variant="ghost"
                      className="w-full text-xs h-8"
                      onClick={() => startOAuth(isGmail ? "google" : "github")}
                    >
                      Reconnect account
                    </Button>
                  </>
                )}
              </CardFooter>
            </Card>
          );
        })}
        {filtered.length === 0 && (
          <div className="col-span-full py-10 text-center text-muted-foreground">
            <p>
              {airGapped
                ? "No local filesystem connector is registered."
                : "No GitHub or Gmail connector is registered."}
            </p>
          </div>
        )}
      </div>

      {airGapped && (
        <Card className="border-border/80 bg-card/60">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              Prepare and save a local dataset
            </CardTitle>
            <CardDescription className="text-xs">
              Pick files from this PC, give the dataset a name, and save it into the sovereign index.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-4 pt-2 space-y-3">
            <Input
              value={datasetTitle}
              onChange={(e) => setDatasetTitle(e.target.value)}
              placeholder="Dataset name"
              className="h-8 text-xs"
            />
            <textarea
              value={datasetNote}
              onChange={(e) => setDatasetNote(e.target.value)}
              placeholder="Optional notes or pasted records to include"
              className="w-full min-h-[72px] rounded-md border border-input bg-background px-3 py-2 text-xs"
            />
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => handleSaveDatasetFiles(e.target.files)}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                className="text-xs h-8"
                disabled={isSavingDataset}
                onClick={() => fileInputRef.current?.click()}
              >
                {isSavingDataset ? (
                  <RefreshCw className="mr-2 h-3 w-3 animate-spin" />
                ) : (
                  <UploadCloud className="mr-2 h-3 w-3" />
                )}
                Pick files and save dataset
              </Button>
              <Button
                variant="outline"
                className="text-xs h-8"
                disabled={isSavingDataset || (!datasetTitle.trim() && !datasetNote.trim())}
                onClick={() => handleSaveDatasetFiles(null)}
              >
                Save notes as dataset
              </Button>
            </div>

            <div className="space-y-2 max-h-48 overflow-y-auto">
              {datasets.length === 0 ? (
                <p className="text-xs text-muted-foreground">No saved datasets yet.</p>
              ) : (
                datasets.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2 text-xs"
                  >
                    <div>
                      <p className="font-medium text-foreground">{item.title}</p>
                      <p className="text-[10px] text-muted-foreground font-mono">{item.id}</p>
                    </div>
                    <Button
                      variant="ghost"
                      className="h-7 w-7 p-0 text-destructive"
                      onClick={() => handleDeleteDataset(item.id, item.title)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <Dialog open={isConfigOpen} onOpenChange={setIsConfigOpen}>
        <DialogContent className="sm:max-w-md bg-card border-border">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {selectedConnector && getIconForConnector(selectedConnector.name)}
              Connect {selectedConnector?.title}
            </DialogTitle>
            <DialogDescription className="text-xs">
              {selectedConnector?.name.toLowerCase().includes("github")
                ? "Paste a GitHub personal access token with repo read access, or use Connect with GitHub."
                : "Configure this connector."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <KeyRound className="h-3 w-3" /> Access Token / Credentials
              </label>
              <Input
                type="password"
                placeholder="ghp_... (GitHub PAT fallback)"
                value={configToken}
                onChange={(e) => setConfigToken(e.target.value)}
                className="text-xs font-mono"
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <Settings className="h-3 w-3" /> Additional Configuration (Optional)
              </label>
              <Input
                placeholder="owner/repo or INBOX,SENT"
                value={configExtra}
                onChange={(e) => setConfigExtra(e.target.value)}
                className="text-xs"
              />
            </div>
          </div>
          <DialogFooter className="sm:justify-between border-t border-border/50 pt-4">
            <Button variant="ghost" onClick={() => setIsConfigOpen(false)} className="h-8 text-xs">
              Cancel
            </Button>
            <Button onClick={handleSaveConfig} className="h-8 text-xs">
              Save Connection
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
