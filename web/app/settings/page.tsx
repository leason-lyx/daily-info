"use client";

import { FormEvent, useEffect, useState } from "react";
import { FlaskConical, Save } from "lucide-react";
import { AiProviderTestResult, api, LlmUsage, LlmUsageBucket } from "@/lib/api";

type Settings = {
  database_url?: string;
  llm_provider_type?: string;
  llm_configured?: boolean;
  llm_base_url?: string | null;
  llm_model_name?: string | null;
  codex_cli_path?: string;
  codex_cli_model?: string | null;
  llm_usage?: LlmUsage;
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>({});
  const [form, setForm] = useState({
    llm_provider_type: "none",
    llm_base_url: "",
    llm_api_key: "",
    llm_model_name: "",
    codex_cli_path: "codex",
    codex_cli_model: "",
  });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<AiProviderTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void loadSettings();
  }, []);

  async function loadSettings() {
    const data = await api.settings();
    const current = data as Settings;
    setSettings(current);
    setError("");
    setForm((prev) => ({
      ...prev,
      llm_provider_type: current.llm_provider_type || "none",
      llm_base_url: current.llm_base_url || "",
      llm_api_key: "",
      llm_model_name: current.llm_model_name || "",
      codex_cli_path: current.codex_cli_path || "codex",
      codex_cli_model: current.codex_cli_model || "",
    }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      await api.patchSettings(settingsBody());
      await loadSettings();
      setTestResult(null);
      setMessage("设置已保存。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function testProvider() {
    setTesting(true);
    setTestResult(null);
    setError("");
    try {
      setTestResult(await api.testAiProvider(settingsBody()));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTesting(false);
    }
  }

  function settingsBody() {
    const body: Record<string, unknown> = {
      llm_provider_type: form.llm_provider_type,
    };
    if (form.llm_provider_type === "openai_compatible") {
      body.llm_base_url = form.llm_base_url || null;
      body.llm_model_name = form.llm_model_name || null;
      if (form.llm_api_key) body.llm_api_key = form.llm_api_key;
    }
    if (form.llm_provider_type === "codex_cli") {
      body.codex_cli_path = form.codex_cli_path || "codex";
      body.codex_cli_model = form.codex_cli_model || null;
    }
    return body;
  }

  const providerLabel = providerDisplayName(form.llm_provider_type);
  const hasSavedApiKey = form.llm_provider_type === "openai_compatible" && Boolean(settings.llm_configured);
  const activeModel = settings.llm_model_name || settings.codex_cli_model || "not set";
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
          <span className={settings.llm_configured ? "statusText good" : "statusText warn"}>{settings.llm_configured ? "configured" : "not configured"}</span>
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
            <select id="settings-provider-type" value={form.llm_provider_type} onChange={(e) => {
              setForm({ ...form, llm_provider_type: e.target.value });
              setTestResult(null);
            }}>
              <option value="none">关闭</option>
              <option value="codex_cli">Codex</option>
              <option value="openai_compatible">自定义 API</option>
            </select>
          </div>
          {form.llm_provider_type === "codex_cli" ? (
            <div className="field">
              <label htmlFor="settings-codex-cli-model">Codex model</label>
              <input id="settings-codex-cli-model" value={form.codex_cli_model} onChange={(e) => setForm({ ...form, codex_cli_model: e.target.value })} />
            </div>
          ) : null}
          {form.llm_provider_type === "openai_compatible" ? (
            <div className="settingsFormGrid">
              <div className="field">
                <label htmlFor="settings-base-url">Base URL</label>
                <input id="settings-base-url" value={form.llm_base_url} onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="settings-model">Model</label>
                <input id="settings-model" value={form.llm_model_name} onChange={(e) => setForm({ ...form, llm_model_name: e.target.value })} />
              </div>
              <div className="field">
                <div className="fieldLabelRow">
                  <label htmlFor="settings-api-key">API key</label>
                  <span className={hasSavedApiKey ? "secretHint saved" : "secretHint"}>{hasSavedApiKey ? "已保存" : "未保存"}</span>
                </div>
                <input
                  id="settings-api-key"
                  type="password"
                  autoComplete="new-password"
                  placeholder={hasSavedApiKey ? "已保存密钥会保留；输入新 key 可替换" : "输入 API key"}
                  value={form.llm_api_key}
                  onChange={(e) => setForm({ ...form, llm_api_key: e.target.value })}
                />
              </div>
            </div>
          ) : null}
          <div className="settingsActions">
            <button className="button" type="button" onClick={testProvider} disabled={testing || form.llm_provider_type === "none"}>
              <FlaskConical size={16} /> {testing ? "Testing" : `Test ${providerLabel}`}
            </button>
            {form.llm_provider_type === "none" ? <span className="subtle">摘要 AI 已关闭，无需测试。</span> : null}
          </div>
          {testResult ? (
            <div className={testResult.ok ? "empty goodNotice" : "empty badNotice"}>
              {testResult.ok
                ? `${providerDisplayName(testResult.provider)} test succeeded in ${testResult.duration_ms}ms.`
                : testResult.error || "Provider test failed."}
            </div>
          ) : null}
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

function providerDisplayName(provider: string | undefined) {
  if (provider === "codex_cli") return "Codex";
  if (provider === "openai_compatible") return "自定义 API";
  return "关闭";
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
