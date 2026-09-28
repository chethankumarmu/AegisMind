import sys

filepath = r'd:\aegisMind\apps\lens\src\components\Connectors.tsx'

content = """import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
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
  type ConnectorInfo,
  type SystemMode
} from "@/lib/api";
import {
  Share2,
  RefreshCw,
  Settings,
  CheckCircle2,
  ExternalLink,
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
  FolderOpen
} from "lucide-react";

const getIconForConnector = (name: string) => {
  if (name.includes("github")) return <Github className="h-5 w-5" />;
  if (name.includes("gmail")) return <Mail className="h-5 w-5" />;
  if (name.includes("local")) return <FolderOpen className="h-5 w-5" />;
  if (name.includes("sqlite") || name.includes("sql")) return <Database className="h-5 w-5" />;
  return <FileText className="h-5 w-5" />;
};

export function Connectors() {
  const [connectors, setConnectors] = React.useState<ConnectorInfo[]>([]);
  const [systemMode, setSystemModeState] = React.useState<SystemMode | null>(null);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [statusFilter, setStatusFilter] = React.useState<string>("all");
  const [selectedConnector, setSelectedConnector] = React.useState<ConnectorInfo | null>(null);
  const [isConfigOpen, setIsConfigOpen] = React.useState(false);
  const [isSyncing, setIsSyncing] = React.useState<Record<string, boolean>>({});
  const [actionNotice, setActionNotice] = React.useState<string | null>(null);
  const [modeToggleLoading, setModeToggleLoading] = React.useState(false);

  // Configuration modal state
  const [configToken, setConfigToken] = React.useState("");
  const [configExtra, setConfigExtra] = React.useState("");

  React.useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [modeRes, listRes] = await Promise.all([
        getSystemMode(),
        listConnectors()
      ]);
      setSystemModeState(modeRes);
      setConnectors(listRes.connectors);
    } catch (e) {
      console.error("Failed to load connectors data", e);
    }
  };

  const handleModeToggle = async (checked: boolean) => {
    setModeToggleLoading(true);
    try {
      const newMode = await setSystemMode(checked);
      setSystemModeState(newMode);
      setActionNotice(`System switched to ${newMode.mode_label}`);
      setTimeout(() => setActionNotice(null), 3000);
      // Reload connectors to reflect any network guard changes
      const listRes = await listConnectors();
      setConnectors(listRes.connectors);
    } catch (e) {
      console.error(e);
      setActionNotice("Failed to toggle mode");
    } finally {
      setModeToggleLoading(false);
    }
  };

  const handleSync = async (connector: ConnectorInfo) => {
    setIsSyncing((prev) => ({ ...prev, [connector.name]: true }));
    setActionNotice(null);
    try {
      const result = await triggerSync(connector.name);
      setActionNotice(`Sync started for ${connector.title}`);
      setTimeout(() => setActionNotice(null), 3000);
      
      // Poll for status update or rely on SSE if implemented. For now, reload after a bit.
      setTimeout(loadData, 2000);
    } catch (e) {
      setActionNotice(`Sync failed for ${connector.title}`);
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

  const handleSaveConfig = async () => {
    if (!selectedConnector) return;
    try {
      await connectConnector(selectedConnector.name, configToken || undefined, {
        extra: configExtra
      });
      setActionNotice(`Connected ${selectedConnector.title} successfully.`);
      setIsConfigOpen(false);
      loadData(); // Refresh statuses
      setTimeout(() => setActionNotice(null), 3000);
    } catch (e) {
      setActionNotice(`Failed to connect ${selectedConnector.title}.`);
    }
  };

  const filtered = (connectors || []).filter((c) => {
    if (!c) return false;
    const title = c.title || c.name || "";
    const description = c.description || "";
    const matchesSearch =
      title.toLowerCase().includes((searchQuery || "").toLowerCase()) ||
      description.toLowerCase().includes((searchQuery || "").toLowerCase());
    const matchesStatus =
      statusFilter === "all" || c.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] p-4 max-w-7xl mx-auto w-full gap-4">
      {/* Top Controls and Filters */}
      <div className="flex flex-col gap-4 rounded-xl border border-border/80 bg-card/60 p-4">
        
        {/* Header row with Mode Switch */}
        <div className="flex items-center justify-between border-b border-border/50 pb-4">
          <div>
            <h2 className="text-xl font-bold text-foreground flex items-center gap-2">
              <Shield className="h-6 w-6 text-primary" />
              Sovereign Connector Marketplace
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              Connect local and enterprise knowledge sources to your private AegisMind knowledge graph.
            </p>
          </div>
          
          {systemMode && (
            <div className="flex items-center gap-3 bg-muted/50 p-2.5 rounded-lg border border-border">
              <div className="flex flex-col items-end">
                <span className="text-sm font-semibold flex items-center gap-1">
                  {systemMode.air_gapped ? <Lock className="h-3.5 w-3.5 text-orange-500" /> : <Globe className="h-3.5 w-3.5 text-green-500" />}
                  {systemMode.mode_label}
                </span>
                <span className="text-xs text-muted-foreground">
                  External connectors {systemMode.external_connectors_enabled ? "enabled" : "disabled"}
                </span>
              </div>
              <Switch 
                checked={systemMode.air_gapped}
                onCheckedChange={handleModeToggle}
                disabled={modeToggleLoading}
              />
            </div>
          )}
        </div>

        {/* Search and Filters */}
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
          <div className="flex gap-1 text-xs p-1 bg-muted/30 rounded-md border border-border/50">
            {["all", "connected", "registered", "error"].map((st) => (
              <button
                key={st}
                type="button"
                onClick={() => setStatusFilter(st)}
                className={`rounded px-3 py-1.5 capitalize transition-colors ${
                  statusFilter === st
                    ? "bg-background text-foreground font-medium shadow-sm border border-border/50"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {st}
              </button>
            ))}
          </div>
        </div>
      </div>

      {actionNotice && (
        <div className="rounded-lg bg-primary/10 border border-primary/30 p-3 text-xs text-primary flex items-center gap-2 animate-in fade-in-0">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          <span>{actionNotice}</span>
        </div>
      )}

      {/* Grid of Connectors */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 overflow-y-auto pr-1 pb-10">
        {filtered.map((connector) => {
          const syncing = isSyncing[connector.name];
          const isConnected = connector.status === "connected";
          const requiresNetwork = connector.spec?.network_required ?? false;
          const isDisabled = systemMode?.air_gapped && requiresNetwork;

          return (
            <Card
              key={connector.name}
              className={`flex flex-col justify-between border-border/70 transition-all ${
                isDisabled ? "bg-muted/20 opacity-75" : "bg-card/40 hover:bg-card/70 hover:border-primary/30"
              }`}
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
                {isDisabled && (
                  <div className="text-[10px] text-orange-500 font-medium flex items-center gap-1 pt-1">
                    <Lock className="w-3 h-3" /> Disabled in Sovereign Mode
                  </div>
                )}
              </CardContent>

              <CardFooter className="p-4 pt-2 border-t border-border/30 flex gap-2">
                {!isConnected ? (
                  <Button 
                    className="w-full text-xs h-8 bg-primary/90 hover:bg-primary" 
                    onClick={() => openConfig(connector)}
                    disabled={isDisabled}
                  >
                    <Power className="mr-2 h-3 w-3" /> Connect
                  </Button>
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
                      disabled={syncing || isDisabled}
                    >
                      <RefreshCw className={`mr-2 h-3 w-3 ${syncing ? "animate-spin" : ""}`} />
                      {syncing ? "Syncing..." : "Sync"}
                    </Button>
                  </>
                )}
              </CardFooter>
            </Card>
          );
        })}
        {filtered.length === 0 && (
          <div className="col-span-full py-10 text-center text-muted-foreground">
            <p>No connectors found.</p>
          </div>
        )}
      </div>

      {/* Configuration Dialog */}
      <Dialog open={isConfigOpen} onOpenChange={setIsConfigOpen}>
        <DialogContent className="sm:max-w-md bg-card border-border">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {selectedConnector && getIconForConnector(selectedConnector.name)}
              Connect {selectedConnector?.title}
            </DialogTitle>
            <DialogDescription className="text-xs">
              {selectedConnector?.spec?.requires_auth 
                ? "Enter your credentials to securely connect this knowledge source." 
                : "Configure the settings for this connector."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <KeyRound className="h-3 w-3" /> Access Token / Credentials
              </label>
              <Input
                type="password"
                placeholder="ghp_... or placeholder token"
                value={configToken}
                onChange={(e) => setConfigToken(e.target.value)}
                className="text-xs font-mono"
              />
              <p className="text-[10px] text-muted-foreground">
                Tokens are stored in the secure backend vault and are never exposed to the LLM.
              </p>
            </div>
            
            <div className="space-y-2">
              <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <Settings className="h-3 w-3" /> Additional Configuration (Optional)
              </label>
              <Input
                placeholder="e.g. repos=org/repo"
                value={configExtra}
                onChange={(e) => setConfigExtra(e.target.value)}
                className="text-xs"
              />
            </div>

            <div className="rounded-md bg-muted/40 p-3 text-xs flex gap-2 border border-border/50">
              <Shield className="h-4 w-4 text-primary shrink-0" />
              <span className="text-muted-foreground leading-relaxed">
                <strong>READ-ONLY Access:</strong> This connector will only read data. AegisMind will never modify your source systems.
              </span>
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
"""

with open(filepath, "w") as f:
    f.write(content)

print("Connectors.tsx updated successfully.")
