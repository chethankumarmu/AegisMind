import sys
import re

filepath = r'd:\aegisMind\apps\lens\src\lib\api.ts'
with open(filepath, 'r') as f:
    content = f.read()

# Replace ConnectorInfo interface
new_connector_info = """export interface ConnectorSpec {
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
}"""

content = re.sub(r'export interface ConnectorInfo \{[^}]+\}', new_connector_info, content, count=1)

new_list_connectors = """export async function listConnectors(): Promise<{ connectors: ConnectorInfo[]; total: number; mode: string }> {
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
        .replace(/\\b\\w/g, (l: string) => l.toUpperCase());

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

export async function checkAccessGraph"""

content = re.sub(r'export async function listConnectors.*?export async function checkAccessGraph', new_list_connectors, content, flags=re.DOTALL)

with open(filepath, 'w') as f:
    f.write(content)
print("API changes applied successfully.")
