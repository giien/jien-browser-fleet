import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Camera,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Database,
  Download,
  ExternalLink,
  FileText,
  Globe2,
  Layers3,
  Mail,
  Megaphone,
  Package,
  PanelRight,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  ShoppingBag,
  Square,
  Store,
  Users,
} from "lucide-react";
import { Badge, Button, EmptyState, Field } from "./components/ui";
import { cn } from "./lib/utils";

type HealthLevel = "good" | "warn" | "risk";

type Commerce = {
  platform?: string;
  brand?: string;
  market?: string;
  owner?: string;
  account_status?: string;
  priority?: string;
  daily_goal?: string;
  notes?: string;
};

type Health = {
  score?: number;
  level?: HealthLevel;
  risks?: string[];
  proxy_country?: string;
  proxy_ip?: string;
  proxy_timezone?: string;
};

type ProxyInfo = {
  mode?: string;
  display?: string;
  last_check?: {
    ok?: boolean;
    ip?: string;
    country?: string;
    timezone?: string;
    message?: string;
  };
};

type Fingerprint = {
  timezone?: string;
  locale?: string;
  screen?: {
    width?: number;
    height?: number;
  };
};

type DeepIssue = {
  level: "info" | "warn" | "risk";
  label: string;
  detail: string;
  penalty: number;
};

type DeepCheck = {
  ok?: boolean;
  score?: number;
  level?: HealthLevel;
  checked_at?: string;
  issues?: DeepIssue[];
  proxy?: {
    ip?: string;
    country?: string;
    city?: string;
    org?: string;
    timezone?: string;
    provider?: string;
  };
  browser?: {
    timezone?: string;
    language?: string;
    platform?: string;
    userAgent?: string;
    webdriver?: boolean;
    screen?: {
      width?: number;
      height?: number;
    };
    viewport?: {
      width?: number;
      height?: number;
    };
  };
};

type Profile = {
  id: string;
  name: string;
  status: "running" | "stopped" | string;
  process_pid?: number | null;
  command_port?: number | null;
  launch_count?: number;
  start_url?: string;
  proxy?: ProxyInfo;
  fingerprint?: Fingerprint;
  commerce?: Commerce;
  health?: Health;
  tags?: string[];
  environment?: {
    last_check?: DeepCheck;
  };
  updated_at?: string;
  last_error?: string | null;
};

type Stats = {
  profiles_total: number;
  profiles_running: number;
  risk_total: number;
  warn_total: number;
  events_total: number;
  max_concurrent_launches: number;
};

type Task = {
  id: string;
  label: string;
  url: string;
};

type TaskGroup = {
  platform: string;
  tasks: Task[];
};

type EventItem = {
  ts: string;
  level: string;
  category: string;
  profile_id?: string | null;
  message: string;
  payload?: Record<string, unknown>;
};

type SectionKey = "accounts" | "health" | "tasks" | "logs";

const DEFAULT_STATS: Stats = {
  profiles_total: 0,
  profiles_running: 0,
  risk_total: 0,
  warn_total: 0,
  events_total: 0,
  max_concurrent_launches: 0,
};

const PLATFORM_LABELS: Record<string, string> = {
  tiktok_shop: "TikTok Shop",
  amazon: "Amazon",
  shopify: "Shopify",
  social: "社媒",
  utility: "工具",
  etsy: "Etsy",
  ebay: "eBay",
};

const MARKET_OPTIONS = ["US", "UK", "CA", "AU", "DE", "FR", "IT", "ES", "JP", "KR"];
const STATUS_OPTIONS = ["normal", "verify", "limited", "suspended"];
const PRIORITY_OPTIONS = ["low", "normal", "high", "urgent"];
const DEFAULT_CAMOUFOX_IMPORT_PATH = "/Volumes/Rtl9210/camoufox-fleet-local";
const NAV_ITEMS: Array<{ key: SectionKey; label: string; icon: typeof Store }> = [
  { key: "accounts", label: "账号矩阵", icon: Layers3 },
  { key: "health", label: "代理健康", icon: ShieldCheck },
  { key: "tasks", label: "任务模板", icon: ClipboardList },
  { key: "logs", label: "运行日志", icon: Activity },
];

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

async function apiText(path: string): Promise<string> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.text();
}

function platformLabel(value?: string) {
  return PLATFORM_LABELS[value || ""] || value || "未分类";
}

function formatTime(value?: string) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function compactId(value?: string | null) {
  if (!value) return "";
  if (value.length <= 18) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function stringifyResult(value: unknown) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function getHealthTone(profile: Profile): HealthLevel {
  return profile.health?.level || "warn";
}

function taskIcon(taskId: string) {
  if (taskId.includes("order")) return ShoppingBag;
  if (taskId.includes("product") || taskId.includes("inventory")) return Package;
  if (taskId.includes("ads")) return Megaphone;
  if (taskId.includes("creator")) return Users;
  if (taskId.includes("gmail")) return Mail;
  if (taskId.includes("ip")) return Globe2;
  return ExternalLink;
}

export default function App() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [stats, setStats] = useState<Stats>(DEFAULT_STATS);
  const [tasks, setTasks] = useState<TaskGroup[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [activeId, setActiveId] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [platformFilter, setPlatformFilter] = useState("all");
  const [healthFilter, setHealthFilter] = useState("all");
  const [marketFilter, setMarketFilter] = useState("all");
  const [urlDrafts, setUrlDrafts] = useState<Record<string, string>>({});
  const [editorDraft, setEditorDraft] = useState<Commerce>({});
  const [output, setOutput] = useState("等待操作...");
  const [busyKey, setBusyKey] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [inputFocused, setInputFocused] = useState(false);
  const [activeSection, setActiveSection] = useState<SectionKey>("accounts");
  const [camoufoxImportPath, setCamoufoxImportPath] = useState(DEFAULT_CAMOUFOX_IMPORT_PATH);
  const [copyBrowserData, setCopyBrowserData] = useState(true);
  const [overwriteBrowserData, setOverwriteBrowserData] = useState(false);

  const activeProfile = useMemo(
    () => profiles.find((profile) => profile.id === activeId) || profiles[0],
    [activeId, profiles],
  );

  const platformOptions = useMemo(() => {
    const values = new Set(profiles.map((profile) => profile.commerce?.platform).filter(Boolean) as string[]);
    return Array.from(values);
  }, [profiles]);

  const quickTasks = useMemo(() => {
    const flattened = tasks.flatMap((group) => group.tasks.map((task) => ({ ...task, platform: group.platform })));
    return flattened.filter((task) => ["check_ip", "gmail", "instagram", "seller_home"].includes(task.id)).slice(0, 6);
  }, [tasks]);

  const visibleProfiles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return profiles.filter((profile) => {
      const commerce = profile.commerce || {};
      const health = getHealthTone(profile);
      const fields = [
        profile.name,
        profile.id,
        commerce.brand,
        commerce.owner,
        commerce.platform,
        commerce.market,
        profile.tags?.join(" "),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return (
        (!needle || fields.includes(needle)) &&
        (statusFilter === "all" || profile.status === statusFilter) &&
        (platformFilter === "all" || commerce.platform === platformFilter) &&
        (healthFilter === "all" || health === healthFilter) &&
        (marketFilter === "all" || commerce.market === marketFilter)
      );
    });
  }, [profiles, query, statusFilter, platformFilter, healthFilter, marketFilter]);

  const activeTasks = useMemo(() => {
    const platform = activeProfile?.commerce?.platform;
    const grouped = tasks.find((group) => group.platform === platform);
    const utility = tasks.find((group) => group.platform === "utility");
    return [...(grouped?.tasks || []), ...(utility?.tasks || [])];
  }, [activeProfile, tasks]);

  const healthProfiles = useMemo(() => {
    const rank: Record<HealthLevel, number> = { risk: 0, warn: 1, good: 2 };
    return [...profiles].sort((a, b) => rank[getHealthTone(a)] - rank[getHealthTone(b)]);
  }, [profiles]);

  async function refreshData(options: { quiet?: boolean } = {}) {
    if (!options.quiet) setBusyKey("refresh");
    try {
      const [nextStats, nextProfiles, nextTasks, nextEvents] = await Promise.all([
        apiJson<Stats>("/api/stats"),
        apiJson<Profile[]>("/api/profiles"),
        apiJson<TaskGroup[]>("/api/commerce/tasks"),
        apiJson<EventItem[]>("/api/events"),
      ]);
      setStats(nextStats);
      setProfiles(nextProfiles);
      setTasks(nextTasks);
      setEvents(nextEvents);
      setUrlDrafts((current) => {
        const next = { ...current };
        nextProfiles.forEach((profile) => {
          if (!next[profile.id]) next[profile.id] = profile.start_url || "https://ipwho.is/";
        });
        return next;
      });
      setActiveId((current) => current || nextProfiles[0]?.id || "");
    } catch (error) {
      setOutput(`刷新失败\n${String(error)}`);
    } finally {
      if (!options.quiet) setBusyKey("");
    }
  }

  useEffect(() => {
    void refreshData();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => {
      if (!inputFocused) void refreshData({ quiet: true });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, inputFocused]);

  useEffect(() => {
    if (activeProfile) {
      setEditorDraft(activeProfile.commerce || {});
    }
  }, [activeProfile?.id]);

  async function runAction(key: string, action: () => Promise<unknown>, refresh = true) {
    setBusyKey(key);
    try {
      const result = await action();
      setOutput(stringifyResult(result));
      if (refresh) await refreshData({ quiet: true });
    } catch (error) {
      setOutput(`操作失败\n${String(error)}`);
    } finally {
      setBusyKey("");
    }
  }

  function toggleSelected(id: string) {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  function toggleAllVisible() {
    const allVisible = visibleProfiles.every((profile) => selectedIds.includes(profile.id));
    if (allVisible) {
      setSelectedIds((current) => current.filter((id) => !visibleProfiles.some((profile) => profile.id === id)));
    } else {
      setSelectedIds((current) => Array.from(new Set([...current, ...visibleProfiles.map((profile) => profile.id)])));
    }
  }

  async function launchProfile(profile: Profile) {
    await runAction(`launch:${profile.id}`, () =>
      apiJson(`/api/profiles/${profile.id}/launch`, {
        method: "POST",
        body: JSON.stringify({ start_url: urlDrafts[profile.id] || profile.start_url || null, headless: false }),
      }),
    );
  }

  async function stopProfile(profile: Profile) {
    await runAction(`stop:${profile.id}`, () => apiJson(`/api/profiles/${profile.id}/stop`, { method: "POST" }));
  }

  async function runTask(profile: Profile, taskId: string) {
    await runAction(`task:${profile.id}:${taskId}`, () =>
      apiJson(`/api/profiles/${profile.id}/task`, {
        method: "POST",
        body: JSON.stringify({ task_id: taskId }),
      }),
    );
  }

  async function batchAction(action: "launch" | "stop" | "proxy" | "sync_timezone" | "deep_check" | "calibrate_environment", ids = selectedIds) {
    await runAction(`batch:${action}`, () =>
      apiJson("/api/profiles/batch", {
        method: "POST",
        body: JSON.stringify({ ids, action }),
      }),
    );
  }

  async function saveCommerce() {
    if (!activeProfile) return;
    await runAction(`save:${activeProfile.id}`, () =>
      apiJson(`/api/profiles/${activeProfile.id}/commerce`, {
        method: "PATCH",
        body: JSON.stringify(editorDraft),
      }),
    );
  }

  async function showLogs(profile: Profile) {
    await runAction(`logs:${profile.id}`, () => apiText(`/api/profiles/${profile.id}/logs`), false);
  }

  async function showPages(profile: Profile) {
    await runAction(`pages:${profile.id}`, () => apiJson(`/api/profiles/${profile.id}/pages`), false);
  }

  async function screenshot(profile: Profile) {
    await runAction(`shot:${profile.id}`, () =>
      apiJson(`/api/profiles/${profile.id}/screenshot`, { method: "POST" }),
    );
  }

  async function proxyCheck(profile: Profile) {
    await runAction(`proxy:${profile.id}`, () =>
      apiJson(`/api/profiles/${profile.id}/proxy-check`, { method: "POST" }),
    );
  }

  async function syncTimezone(profile: Profile) {
    await runAction(`timezone:${profile.id}`, () =>
      apiJson(`/api/profiles/${profile.id}/sync-timezone`, { method: "POST" }),
    );
  }

  async function deepCheck(profile: Profile) {
    await runAction(`deep:${profile.id}`, () =>
      apiJson(`/api/profiles/${profile.id}/deep-check`, { method: "POST" }),
    );
  }

  async function calibrateEnvironment(profile: Profile) {
    await runAction(`calibrate:${profile.id}`, () =>
      apiJson(`/api/profiles/${profile.id}/calibrate-environment`, { method: "POST" }),
    );
  }

  async function importCamoufoxFleet() {
    await runAction("import:camoufox-fleet", () =>
      apiJson("/api/import/camoufox-fleet", {
        method: "POST",
        body: JSON.stringify({
          source_dir: camoufoxImportPath,
          copy_browser_data: copyBrowserData,
          overwrite_browser_data: overwriteBrowserData,
          include_cache: false,
        }),
      }),
    );
  }

  async function exportDataArchive() {
    setBusyKey("export");
    try {
      const response = await fetch("/api/export/archive?include_browser_data=true&include_logs=false&include_screenshots=false&include_cache=false");
      if (!response.ok) throw new Error(await response.text());
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match?.[1] || `jien-browser-data-export-${Date.now()}.zip`;
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setOutput(JSON.stringify({ ok: true, filename, bytes: blob.size }, null, 2));
      await refreshData({ quiet: true });
    } catch (error) {
      setOutput(`导出失败\n${String(error)}`);
    } finally {
      setBusyKey("");
    }
  }

  const runningRatio = stats.profiles_total ? Math.round((stats.profiles_running / stats.profiles_total) * 100) : 0;
  const selectedCount = selectedIds.length;

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <img src="/favicon.svg" alt="" />
          </div>
          <div>
            <strong>极恩跨境指纹浏览器</strong>
            <span>跨境电商运营台</span>
          </div>
        </div>

        <nav className="nav-list" aria-label="运营导航">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={cn("nav-item", activeSection === item.key && "active")}
                type="button"
                onClick={() => setActiveSection(item.key)}
              >
                <Icon size={18} />
                {item.label}
              </button>
            );
          })}
        </nav>

        <section className="side-section">
          <div className="side-title">快捷入口</div>
          <div className="quick-list">
            {quickTasks.map((task) => {
              const Icon = taskIcon(task.id);
              return (
                <button
                  key={`${task.platform}:${task.id}`}
                  type="button"
                  className="quick-item"
                  disabled={!activeProfile || Boolean(busyKey)}
                  onClick={() => activeProfile && void runTask(activeProfile, task.id)}
                  title={task.url}
                >
                  <Icon size={16} />
                  <span>{task.label}</span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="side-section capacity">
          <div>
            <span>并发上限</span>
            <strong>{stats.max_concurrent_launches || "--"}</strong>
          </div>
          <div className="capacity-bar">
            <span style={{ width: `${runningRatio}%` }} />
          </div>
          <small>{stats.profiles_running} 个环境运行中</small>
        </section>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>极恩跨境指纹浏览器</h1>
          </div>
          <div className="top-actions">
            <Button variant={autoRefresh ? "quiet" : "secondary"} onClick={() => setAutoRefresh((value) => !value)}>
              <RefreshCw size={16} className={cn(autoRefresh && "spin-slow")} />
              {autoRefresh ? "自动刷新" : "手动刷新"}
            </Button>
            <Button variant="primary" onClick={() => void refreshData()} disabled={busyKey === "refresh"}>
              <RefreshCw size={16} className={cn(busyKey === "refresh" && "spin")} />
              刷新
            </Button>
          </div>
        </header>

        <section className="data-ops-panel">
          <div className="data-ops-copy">
            <strong>数据迁移</strong>
            <span>从其他本机 Camoufox fleet.db 导入账号和浏览器环境，也可以导出当前系统数据包。</span>
          </div>
          <div className="data-ops-controls">
            <input
              className="path-input"
              value={camoufoxImportPath}
              onChange={(event) => setCamoufoxImportPath(event.target.value)}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              placeholder="/Volumes/Rtl9210/camoufox-fleet-local"
            />
            <label className="check-toggle">
              <input
                type="checkbox"
                checked={copyBrowserData}
                onChange={(event) => setCopyBrowserData(event.target.checked)}
              />
              <span>复制登录环境</span>
            </label>
            <label className="check-toggle">
              <input
                type="checkbox"
                checked={overwriteBrowserData}
                onChange={(event) => setOverwriteBrowserData(event.target.checked)}
              />
              <span>覆盖已存在环境</span>
            </label>
            <Button variant="secondary" onClick={importCamoufoxFleet} disabled={Boolean(busyKey) || !camoufoxImportPath.trim()}>
              <Database size={16} />
              导入 Camoufox DB
            </Button>
            <Button variant="primary" onClick={exportDataArchive} disabled={Boolean(busyKey)}>
              <Download size={16} />
              导出数据
            </Button>
          </div>
        </section>

        <section className="metric-grid" aria-label="账号概览">
          <Metric icon={Store} label="账号总数" value={stats.profiles_total} detail="账号环境已入库" />
          <Metric icon={Play} label="运行中" value={stats.profiles_running} detail={`${runningRatio}% 容量占用`} tone="active" />
          <Metric icon={AlertTriangle} label="风险环境" value={stats.risk_total} detail={`${stats.warn_total} 个需复查`} tone="risk" />
          <Metric icon={Activity} label="事件记录" value={stats.events_total} detail="最近操作已归档" />
        </section>

        <section className="command-strip">
          <div className="search-box">
            <Search size={17} />
            <input
              value={query}
              placeholder="搜索账号、品牌、负责人、标签"
              onChange={(event) => setQuery(event.target.value)}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
            />
          </div>
          <SelectFilter label="状态" value={statusFilter} onChange={setStatusFilter} options={["all", "running", "stopped"]} />
          <SelectFilter label="平台" value={platformFilter} onChange={setPlatformFilter} options={["all", ...platformOptions]} mapper={platformLabel} />
          <SelectFilter label="市场" value={marketFilter} onChange={setMarketFilter} options={["all", ...MARKET_OPTIONS]} />
          <SelectFilter label="健康" value={healthFilter} onChange={setHealthFilter} options={["all", "good", "warn", "risk"]} />
        </section>

        <section className="batch-bar">
          <div>
            <strong>{selectedCount}</strong>
            <span>已选择</span>
          </div>
          <Button size="sm" variant="secondary" disabled={!selectedCount || Boolean(busyKey)} onClick={() => void batchAction("launch")}>
            <Play size={15} />
            批量启动
          </Button>
          <Button size="sm" variant="secondary" disabled={!selectedCount || Boolean(busyKey)} onClick={() => void batchAction("proxy")}>
            <Globe2 size={15} />
            批量验 IP
          </Button>
          <Button size="sm" variant="danger" disabled={!selectedCount || Boolean(busyKey)} onClick={() => void batchAction("stop")}>
            <Square size={15} />
            批量停止
          </Button>
        </section>

        <section className="content-grid">
          {activeSection === "accounts" && (
          <div className="table-panel">
            <div className="panel-title">
              <div>
                <strong>账号环境</strong>
                <span>{visibleProfiles.length} / {profiles.length}</span>
              </div>
              <Badge tone="muted">Camoufox Core</Badge>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="check-col">
                      <input
                        type="checkbox"
                        checked={visibleProfiles.length > 0 && visibleProfiles.every((profile) => selectedIds.includes(profile.id))}
                        onChange={toggleAllVisible}
                        aria-label="选择当前筛选结果"
                      />
                    </th>
                    <th>账号</th>
                    <th>平台</th>
                    <th>状态</th>
                    <th>环境健康</th>
                    <th>入口</th>
                    <th>动作</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleProfiles.map((profile) => (
                    <ProfileRow
                      key={profile.id}
                      profile={profile}
                      active={activeProfile?.id === profile.id}
                      checked={selectedIds.includes(profile.id)}
                      urlValue={urlDrafts[profile.id] || ""}
                      busyKey={busyKey}
                      onSelect={() => setActiveId(profile.id)}
                      onToggle={() => toggleSelected(profile.id)}
                      onUrlChange={(value) => setUrlDrafts((current) => ({ ...current, [profile.id]: value }))}
                      onFocusInput={() => setInputFocused(true)}
                      onBlurInput={() => setInputFocused(false)}
                      onLaunch={() => void launchProfile(profile)}
                      onStop={() => void stopProfile(profile)}
                      onProxy={() => void proxyCheck(profile)}
                      onPages={() => void showPages(profile)}
                      onLogs={() => void showLogs(profile)}
                      onScreenshot={() => void screenshot(profile)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {visibleProfiles.length === 0 && <EmptyState title="没有匹配账号" detail="调整搜索或筛选条件后再试。" />}
          </div>
          )}

          {activeSection === "health" && (
            <div className="table-panel">
              <div className="panel-title">
                <div>
                  <strong>代理健康</strong>
                  <span>按风险优先展示，支持单账号或批量验 IP</span>
                </div>
                <div className="panel-actions">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!visibleProfiles.length || Boolean(busyKey)}
                    onClick={async () => {
                      const ids = visibleProfiles.map((profile) => profile.id);
                      setSelectedIds(ids);
                      await runAction("batch:proxy:visible", () =>
                        apiJson("/api/profiles/batch", {
                          method: "POST",
                          body: JSON.stringify({ ids, action: "proxy" }),
                        }),
                      );
                    }}
                  >
                    <Globe2 size={15} />
                    检测当前筛选
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!visibleProfiles.length || Boolean(busyKey)}
                    onClick={async () => {
                      const ids = visibleProfiles.map((profile) => profile.id);
                      setSelectedIds(ids);
                      await batchAction("sync_timezone", ids);
                    }}
                  >
                    <RefreshCw size={15} />
                    同步当前时区
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={!visibleProfiles.some((profile) => profile.status === "running") || Boolean(busyKey)}
                    onClick={async () => {
                      const ids = visibleProfiles.filter((profile) => profile.status === "running").map((profile) => profile.id);
                      setSelectedIds(ids);
                      await batchAction("deep_check", ids);
                    }}
                  >
                    <ShieldCheck size={15} />
                    深度检测当前
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!visibleProfiles.some((profile) => profile.status === "running") || Boolean(busyKey)}
                    onClick={async () => {
                      const ids = visibleProfiles.filter((profile) => profile.status === "running").map((profile) => profile.id);
                      setSelectedIds(ids);
                      await batchAction("calibrate_environment", ids);
                    }}
                  >
                    <RefreshCw size={15} />
                    校准当前环境
                  </Button>
                </div>
              </div>
              <div className="health-board">
                {healthProfiles.map((profile) => (
                  <div className="health-card" key={profile.id} onClick={() => setActiveId(profile.id)}>
                    <div className="health-card-head">
                      <div>
                        <strong>{profile.name}</strong>
                        <span>{platformLabel(profile.commerce?.platform)} · {profile.commerce?.market || "US"}</span>
                      </div>
                      <div className="score-badges">
                        <Badge tone={getHealthTone(profile)}>{profile.health?.score ?? "--"} 分</Badge>
                        {profile.environment?.last_check?.score !== undefined && (
                          <Badge tone={profile.environment.last_check.level || "warn"}>深检 {profile.environment.last_check.score} 分</Badge>
                        )}
                      </div>
                    </div>
                    <div className="health-meta">
                      <span>{profile.proxy?.display || profile.proxy?.mode || "Direct"}</span>
                      <span>{profile.health?.proxy_ip || profile.proxy?.last_check?.ip || "IP 未检测"}</span>
                      <span>{profile.health?.proxy_country || profile.proxy?.last_check?.country || "国家未知"}</span>
                      <span>代理时区 {profile.health?.proxy_timezone || profile.proxy?.last_check?.timezone || "未知"}</span>
                      <span>指纹时区 {profile.fingerprint?.timezone || "未设置"}</span>
                      <span>{profile.status === "running" ? "运行中可深检" : "启动后可深检"}</span>
                    </div>
                    <div className="risk-list">
                      {[
                        ...(profile.health?.risks || []),
                        ...((profile.environment?.last_check?.issues || [])
                          .filter((issue) => issue.level !== "info")
                          .map((issue) => `${issue.label}：${issue.detail}`)),
                      ].length ? [
                        ...(profile.health?.risks || []),
                        ...((profile.environment?.last_check?.issues || [])
                          .filter((issue) => issue.level !== "info")
                          .map((issue) => `${issue.label}：${issue.detail}`)),
                      ].map((risk) => (
                        <div key={risk}>
                          {getHealthTone(profile) === "good" ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
                          <span>{risk}</span>
                        </div>
                      )) : (
                        <div>
                          <CheckCircle2 size={15} />
                          <span>暂无明显风险</span>
                        </div>
                      )}
                    </div>
                    <div className="health-card-actions">
                      <Button size="sm" variant="secondary" disabled={Boolean(busyKey)} onClick={(event) => {
                        event.stopPropagation();
                        void proxyCheck(profile);
                      }}>
                        <Globe2 size={15} />
                        验 IP
                      </Button>
                      <Button size="sm" variant="secondary" disabled={Boolean(busyKey)} onClick={(event) => {
                        event.stopPropagation();
                        void syncTimezone(profile);
                      }}>
                        <RefreshCw size={15} />
                        同步时区
                      </Button>
                      <Button size="sm" variant="primary" disabled={Boolean(busyKey) || profile.status !== "running"} onClick={(event) => {
                        event.stopPropagation();
                        void deepCheck(profile);
                      }}>
                        <ShieldCheck size={15} />
                        深度检测
                      </Button>
                      <Button size="sm" variant="secondary" disabled={Boolean(busyKey) || profile.status !== "running"} onClick={(event) => {
                        event.stopPropagation();
                        void calibrateEnvironment(profile);
                      }}>
                        <RefreshCw size={15} />
                        校准环境
                      </Button>
                      <Button size="sm" variant="secondary" disabled={Boolean(busyKey)} onClick={(event) => {
                        event.stopPropagation();
                        setActiveId(profile.id);
                        setActiveSection("accounts");
                      }}>
                        <PanelRight size={15} />
                        查看账号
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeSection === "tasks" && (
            <div className="table-panel">
              <div className="panel-title">
                <div>
                  <strong>任务模板库</strong>
                  <span>按平台组织常用入口，点击后对当前选中账号执行</span>
                </div>
                <Badge tone="muted">{activeProfile ? `当前账号：${activeProfile.name}` : "未选择账号"}</Badge>
              </div>
              <div className="task-template-grid">
                {tasks.map((group) => (
                  <div className="task-group-card" key={group.platform}>
                    <div className="task-group-head">
                      <strong>{platformLabel(group.platform)}</strong>
                      <span>{group.tasks.length} 个模板</span>
                    </div>
                    <div className="task-list">
                      {group.tasks.map((task) => {
                        const Icon = taskIcon(task.id);
                        const matchingProfile =
                          group.platform === "utility"
                            ? activeProfile
                            : profiles.find((profile) => profile.commerce?.platform === group.platform);
                        return (
                          <button
                            type="button"
                            className="task-template"
                            key={`${group.platform}:${task.id}`}
                            disabled={!matchingProfile || Boolean(busyKey)}
                            onClick={() => {
                              if (!matchingProfile) return;
                              setActiveId(matchingProfile.id);
                              void runTask(matchingProfile, task.id);
                            }}
                            title={task.url}
                          >
                            <Icon size={17} />
                            <span>
                              <strong>{task.label}</strong>
                              <small>{task.url}</small>
                            </span>
                            <ExternalLink size={15} />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeSection === "logs" && (
            <div className="table-panel">
              <div className="panel-title">
                <div>
                  <strong>运行日志</strong>
                  <span>最近事件、账号动作和批量任务记录</span>
                </div>
                <Button size="sm" variant="secondary" disabled={Boolean(busyKey)} onClick={() => void refreshData()}>
                  <RefreshCw size={15} />
                  刷新日志
                </Button>
              </div>
              <div className="log-board">
                {events.map((event) => (
                  <div className="log-row" key={`${event.ts}:${event.category}:${event.message}:${event.profile_id || ""}`}>
                    <span>{formatTime(event.ts)}</span>
                    <Badge tone={event.level === "error" ? "risk" : "muted"}>{event.category}</Badge>
                    <strong>{event.message}</strong>
                    <small>{event.profile_id || "system"}</small>
                  </div>
                ))}
                {events.length === 0 && <EmptyState title="暂无事件" detail="启动、停止、检测代理后会在这里出现记录。" />}
              </div>
            </div>
          )}

          <aside className="inspector">
            <div className="panel-title">
              <div>
                <strong>运营字段</strong>
                <span>{activeProfile?.name || "未选择账号"}</span>
              </div>
              <PanelRight size={18} />
            </div>

            {activeProfile ? (
              <>
                <div className="profile-summary">
                  <div>
                    <strong>{activeProfile.name}</strong>
                    <span>{activeProfile.id}</span>
                  </div>
                  <Badge tone={getHealthTone(activeProfile)}>{activeProfile.health?.score ?? "--"} 分</Badge>
                </div>

                <div className="health-box">
                  {(activeProfile.health?.risks || []).length ? (
                    activeProfile.health?.risks?.map((risk) => (
                      <div key={risk}>
                        <AlertTriangle size={15} />
                        <span>{risk}</span>
                      </div>
                    ))
                  ) : (
                    <div>
                      <CheckCircle2 size={15} />
                      <span>暂无明显风险</span>
                    </div>
                  )}
                </div>

                {activeProfile.environment?.last_check && (
                  <div className="deep-summary">
                    <div className="deep-summary-head">
                      <strong>深度环境检测</strong>
                      <Badge tone={activeProfile.environment.last_check.level || "warn"}>
                        {activeProfile.environment.last_check.score ?? "--"} 分
                      </Badge>
                    </div>
                    <div className="deep-summary-grid">
                      <span>IP {activeProfile.environment.last_check.proxy?.ip || "--"}</span>
                      <span>代理时区 {activeProfile.environment.last_check.proxy?.timezone || "--"}</span>
                      <span>浏览器时区 {activeProfile.environment.last_check.browser?.timezone || "--"}</span>
                      <span>语言 {activeProfile.environment.last_check.browser?.language || "--"}</span>
                    </div>
                    {(activeProfile.environment.last_check.issues || []).slice(0, 3).map((issue) => (
                      <div className="deep-issue" key={`${issue.label}:${issue.detail}`}>
                        <AlertTriangle size={14} />
                        <span>{issue.label}：{issue.detail}</span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="editor-grid">
                  <Field label="平台">
                    <select
                      value={editorDraft.platform || "tiktok_shop"}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, platform: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    >
                      {["tiktok_shop", "amazon", "shopify", "social", "etsy", "ebay"].map((platform) => (
                        <option key={platform} value={platform}>
                          {platformLabel(platform)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="市场">
                    <select
                      value={editorDraft.market || "US"}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, market: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    >
                      {MARKET_OPTIONS.map((market) => (
                        <option key={market}>{market}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="品牌">
                    <input
                      value={editorDraft.brand || ""}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, brand: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    />
                  </Field>
                  <Field label="负责人">
                    <input
                      value={editorDraft.owner || ""}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, owner: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    />
                  </Field>
                  <Field label="账号状态">
                    <select
                      value={editorDraft.account_status || "normal"}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, account_status: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    >
                      {STATUS_OPTIONS.map((status) => (
                        <option key={status}>{status}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="优先级">
                    <select
                      value={editorDraft.priority || "normal"}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, priority: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    >
                      {PRIORITY_OPTIONS.map((priority) => (
                        <option key={priority}>{priority}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="今日目标" className="full">
                    <input
                      value={editorDraft.daily_goal || ""}
                      placeholder="例如：处理订单、检查广告预算、上新 3 个商品"
                      onChange={(event) => setEditorDraft((current) => ({ ...current, daily_goal: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    />
                  </Field>
                  <Field label="备注" className="full">
                    <textarea
                      value={editorDraft.notes || ""}
                      rows={3}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, notes: event.target.value }))}
                      onFocus={() => setInputFocused(true)}
                      onBlur={() => setInputFocused(false)}
                    />
                  </Field>
                </div>

                <div className="inspector-actions">
                  <div className="inspector-action-main">
                    <Button variant="primary" onClick={() => void saveCommerce()} disabled={Boolean(busyKey)}>
                      <CheckCircle2 size={16} />
                      保存字段
                    </Button>
                    <Button variant="secondary" onClick={() => activeProfile && void deepCheck(activeProfile)} disabled={Boolean(busyKey) || activeProfile.status !== "running"}>
                      <ShieldCheck size={16} />
                      深度检测
                    </Button>
                    <Button variant="secondary" onClick={() => activeProfile && void calibrateEnvironment(activeProfile)} disabled={Boolean(busyKey) || activeProfile.status !== "running"}>
                      <RefreshCw size={16} />
                      校准环境
                    </Button>
                  </div>
                  <div className="task-shortcuts">
                    {activeTasks.slice(0, 5).map((task) => {
                      const Icon = taskIcon(task.id);
                      return (
                        <Button key={task.id} variant="secondary" onClick={() => void runTask(activeProfile, task.id)} disabled={Boolean(busyKey)}>
                          <Icon size={16} />
                          <span>{task.label}</span>
                        </Button>
                      );
                    })}
                  </div>
                </div>
              </>
            ) : (
              <EmptyState title="请选择账号" detail="选择表格中的一行后编辑运营字段。" />
            )}

            <div className="output-panel">
              <div className="panel-title compact">
                <strong>操作输出</strong>
                <FileText size={16} />
              </div>
              <pre>{output}</pre>
            </div>

            <div className="event-panel">
              <div className="panel-title compact">
                <strong>最近事件</strong>
                <Activity size={16} />
              </div>
              <div className="event-list">
                {events.slice(0, 8).map((event) => (
                  <div className="event-item" key={`${event.ts}:${event.message}:${event.profile_id || ""}`}>
                    <span className="event-time">{formatTime(event.ts)}</span>
                    <div className="event-copy">
                      <strong>{event.message}</strong>
                      <small>
                        <span>{event.category}</span>
                        {event.profile_id && <code title={event.profile_id}>{compactId(event.profile_id)}</code>}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </aside>
        </section>
      </section>
    </main>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
  tone = "neutral",
}: {
  icon: typeof Store;
  label: string;
  value: number;
  detail: string;
  tone?: "neutral" | "active" | "risk";
}) {
  return (
    <div className={cn("metric", `metric-${tone}`)}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
      <Icon size={22} />
    </div>
  );
}

function SelectFilter({
  label,
  value,
  onChange,
  options,
  mapper,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  mapper?: (value: string) => string;
}) {
  return (
    <label className="select-filter">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option value={option} key={option}>
            {option === "all" ? "全部" : mapper ? mapper(option) : option}
          </option>
        ))}
      </select>
      <ChevronDown size={14} />
    </label>
  );
}

function ProfileRow({
  profile,
  active,
  checked,
  urlValue,
  busyKey,
  onSelect,
  onToggle,
  onUrlChange,
  onFocusInput,
  onBlurInput,
  onLaunch,
  onStop,
  onProxy,
  onPages,
  onLogs,
  onScreenshot,
}: {
  profile: Profile;
  active: boolean;
  checked: boolean;
  urlValue: string;
  busyKey: string;
  onSelect: () => void;
  onToggle: () => void;
  onUrlChange: (value: string) => void;
  onFocusInput: () => void;
  onBlurInput: () => void;
  onLaunch: () => void;
  onStop: () => void;
  onProxy: () => void;
  onPages: () => void;
  onLogs: () => void;
  onScreenshot: () => void;
}) {
  const commerce = profile.commerce || {};
  const health = getHealthTone(profile);
  const isRunning = profile.status === "running";
  const disabled = Boolean(busyKey);
  const screen = profile.fingerprint?.screen;

  return (
    <tr className={cn(active && "active-row")} onClick={onSelect}>
      <td className="check-col">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          onClick={(event) => event.stopPropagation()}
          aria-label={`选择 ${profile.name}`}
        />
      </td>
      <td>
        <div className="account-cell">
          <strong>{profile.name}</strong>
          <span>{commerce.brand || "未设置品牌"} · {commerce.owner || "未分配"}</span>
          <small>{profile.tags?.join(" / ") || profile.id}</small>
        </div>
      </td>
      <td>
        <div className="stack-cell">
          <Badge tone="neutral">{platformLabel(commerce.platform)}</Badge>
          <span>{commerce.market || "US"} · {commerce.priority || "normal"}</span>
        </div>
      </td>
      <td>
        <div className="stack-cell">
          <Badge tone={isRunning ? "active" : "muted"}>{isRunning ? "运行中" : "已停止"}</Badge>
          <span>PID {profile.process_pid || "--"} · 端口 {profile.command_port || "--"}</span>
        </div>
      </td>
      <td>
        <div className="health-cell">
          <Badge tone={health}>{profile.health?.score ?? "--"} 分</Badge>
          <span>{profile.health?.risks?.[0] || "环境正常"}</span>
          <small>{profile.proxy?.display || profile.proxy?.mode || "Direct"} · {profile.fingerprint?.timezone || "--"} · {screen?.width || "--"}x{screen?.height || "--"}</small>
        </div>
      </td>
      <td>
        <input
          className="url-input"
          value={urlValue}
          onChange={(event) => onUrlChange(event.target.value)}
          onFocus={onFocusInput}
          onBlur={onBlurInput}
          onClick={(event) => event.stopPropagation()}
          placeholder="https://"
        />
      </td>
      <td>
        <div className="row-actions" onClick={(event) => event.stopPropagation()}>
          {isRunning ? (
            <Button size="icon" variant="danger" onClick={onStop} disabled={disabled} title="停止">
              <Square size={15} />
            </Button>
          ) : (
            <Button size="icon" variant="primary" onClick={onLaunch} disabled={disabled} title="启动">
              <Play size={15} />
            </Button>
          )}
          <Button size="icon" variant="secondary" onClick={onProxy} disabled={disabled} title="检查 IP">
            <Globe2 size={15} />
          </Button>
          <Button size="icon" variant="secondary" onClick={onPages} disabled={disabled || !isRunning} title="页面">
            <ExternalLink size={15} />
          </Button>
          <Button size="icon" variant="secondary" onClick={onScreenshot} disabled={disabled || !isRunning} title="截图">
            <Camera size={15} />
          </Button>
          <Button size="icon" variant="secondary" onClick={onLogs} disabled={disabled} title="日志">
            <FileText size={15} />
          </Button>
        </div>
      </td>
    </tr>
  );
}
