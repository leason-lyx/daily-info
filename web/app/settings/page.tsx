"use client";

import { FormEvent, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, FlaskConical, Plus, Save, Trash2 } from "lucide-react";
import { AiProviderTestResult, api, LlmProvider, LlmUsage, LlmUsageBucket } from "@/lib/api";

type Settings = {
  database_url?: string;
  llm_provider_type?: string;
  llm_configured?: boolean;
  llm_base_url?: string | null;
  llm_model_name?: string | null;
  codex_cli_path?: string;
  codex_cli_model?: string | null;
  llm_providers?: LlmProvider[];
  llm_usage?: LlmUsage;
};

type ProviderForm = {
  clientId: string;
  id?: number;
  name: string;
  base_url: string;
  api_key: string;
  model_name: string;
  temperature: string;
  timeout: string;
  enabled: boolean;
  priority: number;
  has_api_key: boolean;
  last_error?: string;
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>({});
  const [form, setForm] = useState({
    llm_provider_type: "none",
    codex_cli_path: "codex",
    codex_cli_model: "",
  });
  const [providers, setProviders] = useState<ProviderForm[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [codexTestResult, setCodexTestResult] = useState<AiProviderTestResult | null>(null);
  const [providerTestResults, setProviderTestResults] = useState<Record<string, AiProviderTestResult>>({});
  const [testingId, setTestingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void loadSettings();
  }, []);

  async function loadSettings() {
    const data = await api.settings();
    const current = data as Settings;
    setSettings(current);
    setError("");
    setForm({
      llm_provider_type: current.llm_provider_type || "none",
      codex_cli_path: current.codex_cli_path || "codex",
      codex_cli_model: current.codex_cli_model || "",
    });
    setProviders(providerForms(current));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      await api.patchSettings(settingsBody());
      await loadSettings();
      setCodexTestResult(null);
      setProviderTestResults({});
      setMessage("设置已保存。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function testCodex() {
    setTestingId("codex");
    setCodexTestResult(null);
    setError("");
    try {
      setCodexTestResult(await api.testAiProvider(settingsBody()));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTestingId(null);
    }
  }

  async function testProvider(provider: ProviderForm, index: number) {
    setTestingId(provider.clientId);
    setProviderTestResults((prev) => {
      const next = { ...prev };
      delete next[provider.clientId];
      return next;
    });
    setError("");
    try {
      const result = await api.testAiProvider({
        llm_provider_type: "openai_compatible",
        llm_providers: [providerPayload(provider, index)],
      });
      setProviderTestResults((prev) => ({ ...prev, [provider.clientId]: result }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTestingId(null);
    }
  }

  function settingsBody() {
    const body: Record<string, unknown> = {
      llm_provider_type: form.llm_provider_type,
    };
    if (form.llm_provider_type === "openai_compatible") {
      body.llm_providers = providers.map(providerPayload);
    }
    if (form.llm_provider_type === "codex_cli") {
      body.codex_cli_path = form.codex_cli_path || "codex";
      body.codex_cli_model = form.codex_cli_model || null;
    }
    return body;
  }

  function addProvider() {
    setProviders((current) => [
      ...current,
      {
        clientId: newProviderId(),
        name: `Custom API ${current.length + 1}`,
        base_url: "",
        api_key: "",
        model_name: "",
        temperature: "0.2",
        timeout: "60",
        enabled: true,
        priority: current.length,
        has_api_key: false,
      },
    ]);
  }

  function updateProvider(clientId: string, patch: Partial<ProviderForm>) {
    setProviders((current) => current.map((provider) => (provider.clientId === clientId ? { ...provider, ...patch } : provider)));
  }

  function removeProvider(clientId: string) {
    setProviders((current) => current.filter((provider) => provider.clientId !== clientId));
    setProviderTestResults((prev) => {
      const next = { ...prev };
      delete next[clientId];
      return next;
    });
  }

  function moveProvider(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= providers.length) return;
    setProviders((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  const savedActiveProviders = [...(settings.llm_providers || [])].sort((a, b) => a.priority - b.priority || a.id - b.id).filter((provider) => provider.enabled);
  const activeModel = settings.llm_provider_type === "openai_compatible"
    ? savedActiveProviders[0]?.model_name || "not set"
    : settings.llm_model_name || settings.codex_cli_model || "not set";
  const database = formatDatabase(settings.database_url);

  return (
    <div className="settingsPage">
      <header className="pageHead">
        <div>
          <h1>Settings</h1>
        </div>
      </header>
      {error && <div className="empty">{error}</div>}
      <section className="settingsOverview" aria-label="Runtime overview">
        <div className="settingsStatLine">
          <span className="settingsStatLabel">Database</span>
          <span className="settingsStatValue" title={settings.database_url || "unknown"}>{database.kind} · {database.path}</span>
        </div>
        <div className="settingsStatLine">
          <span className="settingsStatLabel">Summary AI</span>
          <span className="settingsStatValue">{providerDisplayName(settings.llm_provider_type)} · {activeModel}</span>
          <span className={settings.llm_configured ? "statusText good" : "statusText warn"}>
            {settings.llm_configured ? "configured" : "not configured"}
            {settings.llm_provider_type === "openai_compatible" ? ` · ${savedActiveProviders.length} enabled` : ""}
          </span>
        </div>
      </section>

      <section className="settingsSection">
        <div className="settingsSectionHead">
          <h2>Custom API usage</h2>
        </div>
        {settings.llm_usage?.all_time || settings.llm_provider_type === "openai_compatible" ? (
          <div className="settingsBlock">
            {settings.llm_provider_type !== "openai_compatible" ? <span className="statusText">inactive provider</span> : null}
            <div className="usageStrip">
              <UsageCell label="24h" bucket={settings.llm_usage?.recent_24h} />
              <UsageCell label="7d" bucket={settings.llm_usage?.recent_7d} />
              <UsageCell label="All time" bucket={settings.llm_usage?.all_time} />
            </div>
            <div className="settingsFootnote">
              <span>last used {formatDate(settings.llm_usage?.last_used_at)}</span>
              {settings.llm_usage?.last_error ? <span className="statusText bad">last error {formatDate(settings.llm_usage.last_error_at)}</span> : null}
            </div>
            {settings.llm_usage?.last_error ? <span className="sourceError">{settings.llm_usage.last_error}</span> : null}
          </div>
        ) : (
          <div className="empty">No custom API usage for the active provider.</div>
        )}
      </section>

      <form className="settingsSection" onSubmit={save}>
        <section className="settingsBlock">
          <div className="settingsSectionHead withStatus">
            <div>
              <h2>摘要 AI 设置</h2>
            </div>
            <span className={settings.llm_configured ? "statusText good" : "statusText warn"}>{settings.llm_configured ? "configured" : "not configured"}</span>
          </div>
          <div className="field">
            <label htmlFor="settings-provider-type">摘要 AI</label>
            <select
              id="settings-provider-type"
              value={form.llm_provider_type}
              onChange={(e) => {
                setForm({ ...form, llm_provider_type: e.target.value });
                setCodexTestResult(null);
                setProviderTestResults({});
              }}
            >
              <option value="none">关闭</option>
              <option value="codex_cli">Codex</option>
              <option value="openai_compatible">自定义 API</option>
            </select>
          </div>
          {form.llm_provider_type === "codex_cli" ? (
            <>
              <div className="field">
                <label htmlFor="settings-codex-cli-model">Codex model</label>
                <input id="settings-codex-cli-model" value={form.codex_cli_model} onChange={(e) => setForm({ ...form, codex_cli_model: e.target.value })} />
              </div>
              <div className="settingsActions">
                <button className="button" type="button" onClick={testCodex} disabled={testingId === "codex"}>
                  <FlaskConical size={16} /> {testingId === "codex" ? "Testing" : "Test Codex"}
                </button>
              </div>
              {codexTestResult ? <TestNotice result={codexTestResult} /> : null}
            </>
          ) : null}
          {form.llm_provider_type === "openai_compatible" ? (
            <div className="settingsBlock">
              <div className="settingsSectionHead withStatus">
                <h3>自定义 API 配置档</h3>
                <button className="button" type="button" onClick={addProvider}>
                  <Plus size={16} /> Add API
                </button>
              </div>
              <div className="providerList">
                {providers.length ? providers.map((provider, index) => (
                  <div className="providerRow" key={provider.clientId}>
                    <div className="providerOrderControls">
                      <button className="iconButton" type="button" title="Move up" onClick={() => moveProvider(index, -1)} disabled={index === 0}>
                        <ArrowUp size={16} />
                      </button>
                      <button className="iconButton" type="button" title="Move down" onClick={() => moveProvider(index, 1)} disabled={index === providers.length - 1}>
                        <ArrowDown size={16} />
                      </button>
                    </div>
                    <div className="providerFields">
                      <div className="providerTopLine">
                        <label className="checkLabel">
                          <input type="checkbox" checked={provider.enabled} onChange={(e) => updateProvider(provider.clientId, { enabled: e.target.checked })} />
                          Enabled
                        </label>
                        <span className="statusText">#{index + 1}</span>
                        {provider.has_api_key || provider.api_key ? <span className="secretHint saved">key saved</span> : <span className="secretHint">no key</span>}
                      </div>
                      <div className="settingsProviderGrid">
                        <div className="field">
                          <label htmlFor={`provider-name-${provider.clientId}`}>Name</label>
                          <input id={`provider-name-${provider.clientId}`} value={provider.name} onChange={(e) => updateProvider(provider.clientId, { name: e.target.value })} />
                        </div>
                        <div className="field">
                          <label htmlFor={`provider-url-${provider.clientId}`}>Base URL</label>
                          <input id={`provider-url-${provider.clientId}`} value={provider.base_url} onChange={(e) => updateProvider(provider.clientId, { base_url: e.target.value })} />
                        </div>
                        <div className="field">
                          <label htmlFor={`provider-model-${provider.clientId}`}>Model</label>
                          <input id={`provider-model-${provider.clientId}`} value={provider.model_name} onChange={(e) => updateProvider(provider.clientId, { model_name: e.target.value })} />
                        </div>
                        <div className="field">
                          <label htmlFor={`provider-key-${provider.clientId}`}>API key</label>
                          <input
                            id={`provider-key-${provider.clientId}`}
                            type="password"
                            autoComplete="new-password"
                            placeholder={provider.has_api_key ? "已保存密钥会保留；输入新 key 可替换" : "输入 API key"}
                            value={provider.api_key}
                            onChange={(e) => updateProvider(provider.clientId, { api_key: e.target.value })}
                          />
                        </div>
                        <div className="field">
                          <label htmlFor={`provider-temp-${provider.clientId}`}>Temperature</label>
                          <input id={`provider-temp-${provider.clientId}`} type="number" min="0" max="2" step="0.1" value={provider.temperature} onChange={(e) => updateProvider(provider.clientId, { temperature: e.target.value })} />
                        </div>
                        <div className="field">
                          <label htmlFor={`provider-timeout-${provider.clientId}`}>Timeout seconds</label>
                          <input id={`provider-timeout-${provider.clientId}`} type="number" min="1" max="300" value={provider.timeout} onChange={(e) => updateProvider(provider.clientId, { timeout: e.target.value })} />
                        </div>
                      </div>
                      {provider.last_error ? <span className="sourceError">{provider.last_error}</span> : null}
                      {providerTestResults[provider.clientId] ? <TestNotice result={providerTestResults[provider.clientId]} /> : null}
                    </div>
                    <div className="providerActions">
                      <button className="iconButton" type="button" title="Test API" onClick={() => testProvider(provider, index)} disabled={testingId === provider.clientId}>
                        <FlaskConical size={16} />
                      </button>
                      <button className="iconButton danger" type="button" title="Delete API" onClick={() => removeProvider(provider.clientId)}>
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                )) : <div className="empty">No custom API profiles. Add one to enable OpenAI-compatible summaries.</div>}
              </div>
            </div>
          ) : null}
          {form.llm_provider_type === "none" ? <span className="subtle">摘要 AI 已关闭，无需测试。</span> : null}
        </section>

        <div className="settingsSaveBar">
          <button className="button primary" type="submit" disabled={saving}>
            <Save size={16} /> {saving ? "Saving" : "Save"}
          </button>
        </div>
      </form>
      {message && <div className="empty goodNotice">{message}</div>}
    </div>
  );
}

function providerPayload(provider: ProviderForm, index: number) {
  return {
    id: provider.id,
    name: provider.name,
    provider_type: "openai_compatible",
    base_url: provider.base_url,
    api_key: provider.api_key,
    model_name: provider.model_name,
    temperature: Number(provider.temperature) || 0.2,
    timeout: Number(provider.timeout) || 60,
    enabled: provider.enabled,
    priority: index,
  };
}

function providerForms(settings: Settings): ProviderForm[] {
  const rows = [...(settings.llm_providers || [])].sort((a, b) => a.priority - b.priority || a.id - b.id);
  return rows.map((provider, index) => ({
    clientId: provider.id ? `provider-${provider.id}` : newProviderId(),
    id: provider.id || undefined,
    name: provider.name || `Custom API ${index + 1}`,
    base_url: provider.base_url || "",
    api_key: "",
    model_name: provider.model_name || "",
    temperature: String(provider.temperature ?? 0.2),
    timeout: String(provider.timeout ?? 60),
    enabled: Boolean(provider.enabled),
    priority: provider.priority ?? index,
    has_api_key: Boolean(provider.has_api_key),
    last_error: provider.last_error || "",
  }));
}

function newProviderId() {
  return `new-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function providerDisplayName(provider: string | undefined) {
  if (provider === "codex_cli") return "Codex";
  if (provider === "openai_compatible") return "自定义 API";
  return "关闭";
}

function TestNotice({ result }: { result: AiProviderTestResult }) {
  return (
    <div className={result.ok ? "empty goodNotice" : "empty badNotice"}>
      {result.ok
        ? `${providerDisplayName(result.provider)} test succeeded in ${result.duration_ms}ms.`
        : result.error || "Provider test failed."}
    </div>
  );
}

function UsageCell({ label, bucket }: { label: string; bucket?: LlmUsageBucket }) {
  const data = bucket || emptyUsage();
  return (
    <div className="usageCell">
      <span className="usageLabel">{label}</span>
      <strong>{formatNumber(data.total_tokens)} tokens</strong>
      <div className="usageMain">
        <span>{formatNumber(data.requests)} requests</span>
        <span>{formatNumber(data.success)} ok</span>
        <span>{formatNumber(data.failed)} failed</span>
      </div>
      <div className="usageDetail">
        <span>{formatNumber(data.prompt_tokens)} prompt</span>
        <span>{formatNumber(data.completion_tokens)} completion</span>
        <span>{formatNumber(data.reasoning_tokens)} reasoning</span>
      </div>
    </div>
  );
}

function emptyUsage(): LlmUsageBucket {
  return {
    requests: 0,
    success: 0,
    failed: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    reasoning_tokens: 0,
    duration_ms: 0,
  };
}

function formatNumber(value: number | undefined) {
  return new Intl.NumberFormat("en").format(value || 0);
}

function formatDate(value: string | null | undefined) {
  if (!value) return "never";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDatabase(value: string | undefined) {
  if (!value) return { kind: "Database", path: "unknown" };
  const sqlitePrefix = "sqlite:////";
  if (value.startsWith(sqlitePrefix)) return { kind: "SQLite", path: `/${value.slice(sqlitePrefix.length)}` };
  if (value.startsWith("sqlite:///")) return { kind: "SQLite", path: value.slice("sqlite:///".length) };
  return { kind: "Database", path: value };
}
