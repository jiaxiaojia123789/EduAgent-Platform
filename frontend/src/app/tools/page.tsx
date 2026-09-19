"use client";

import React, { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import {
  ArrowLeft, Database, Search, Plus, Check, X, Send, RefreshCw, Wrench, Sparkles,
  Sigma, Table2, Code2, GraduationCap, BookOpen, Globe, Download, CloudSun, Languages,
  Subtitles, ImagePlus, Presentation, Network, School, Blocks, BookOpenText, ListChecks,
  MonitorPlay, FileQuestion, Table, Gauge, PenLine, BarChart3, ClipboardList,
  Lightbulb, Users, FileDown, ChevronRight,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type ToolKind = "mcp" | "skill";

interface MCPTool {
  id: string;
  server: string;
  name: string;
  category: string;
  icon: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, { type: string; description?: string }>;
  };
  example_arguments: Record<string, string>;
}

interface Skill {
  id: string;
  name: string;
  category: string;
  icon: string;
  description: string;
  input_label: string;
  enabled: boolean;
}

interface MyTools {
  mcp: string[];
  skill: string[];
}

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  Sigma, Table2, Code2, GraduationCap, BookOpen, Globe, Download, CloudSun, Languages,
  Subtitles, ImagePlus, Presentation, Network, School, Blocks, BookOpenText, ListChecks,
  MonitorPlay, FileQuestion, Table, Gauge, PenLine, BarChart3, ClipboardList,
  Lightbulb, Users, FileDown, Wrench, Sparkles,
};

const Icon: React.FC<{ name: string; className?: string }> = ({ name, className }) => {
  const Cmp = ICONS[name] || Wrench;
  return <Cmp className={className} />;
};

const MY_TOOLS_KEY = "my_tools";

export default function ToolsCenterPage() {
  const [activeTab, setActiveTab] = useState<ToolKind>("mcp");
  const [mcpTools, setMcpTools] = useState<MCPTool[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState<string>("全部");
  const [onlyMine, setOnlyMine] = useState(false);
  const [myTools, setMyTools] = useState<MyTools>({ mcp: [], skill: [] });

  // Drawer state
  const [drawerKind, setDrawerKind] = useState<ToolKind>("mcp");
  const [drawerItem, setDrawerItem] = useState<MCPTool | Skill | null>(null);
  const [mcpArgs, setMcpArgs] = useState<Record<string, string>>({});
  const [skillTopic, setSkillTopic] = useState("");
  const [skillSubject, setSkillSubject] = useState("高中数学");
  const [skillGrade, setSkillGrade] = useState("高二");
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  useEffect(() => {
    fetchTools();
    try {
      const raw = localStorage.getItem(MY_TOOLS_KEY);
      if (raw) setMyTools(JSON.parse(raw));
    } catch {}
  }, []);

  const fetchTools = async () => {
    try {
      const [tRes, sRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/v1/ext/mcp/tools`).then((r) => r.json()),
        fetch(`${API_BASE_URL}/api/v1/ext/skills`).then((r) => r.json()),
      ]);
      setMcpTools(tRes.mcp_tools || []);
      setSkills(sRes.skills || []);
    } catch (e) {
      console.error("加载工具失败:", e);
    }
  };

  const categories = useMemo(() => {
    const src = activeTab === "mcp" ? mcpTools : skills;
    return ["全部", ...Array.from(new Set(src.map((x: any) => x.category)))];
  }, [activeTab, mcpTools, skills]);

  const filtered = useMemo(() => {
    const src: any[] = activeTab === "mcp" ? mcpTools : skills;
    const mineIds = activeTab === "mcp" ? myTools.mcp : myTools.skill;
    const kw = search.trim().toLowerCase();
    return src.filter((x) => {
      if (activeCategory !== "全部" && x.category !== activeCategory) return false;
      if (onlyMine && !mineIds.includes(x.id)) return false;
      if (kw && !`${x.name} ${x.description} ${x.category}`.toLowerCase().includes(kw)) return false;
      return true;
    });
  }, [activeTab, mcpTools, skills, activeCategory, onlyMine, search, myTools]);

  const isMine = (kind: ToolKind, id: string) =>
    kind === "mcp" ? myTools.mcp.includes(id) : myTools.skill.includes(id);

  const toggleMine = (kind: ToolKind, id: string) => {
    setMyTools((prev) => {
      const list = kind === "mcp" ? [...prev.mcp] : [...prev.skill];
      const idx = list.indexOf(id);
      if (idx >= 0) list.splice(idx, 1);
      else list.push(id);
      const next = kind === "mcp" ? { ...prev, mcp: list } : { ...prev, skill: list };
      localStorage.setItem(MY_TOOLS_KEY, JSON.stringify(next));
      return next;
    });
  };

  const openDrawer = (kind: ToolKind, item: any) => {
    setDrawerKind(kind);
    setDrawerItem(item);
    setRunResult(null);
    if (kind === "mcp") {
      const args: Record<string, string> = {};
      Object.keys(item.parameters?.properties || {}).forEach((k) => {
        args[k] = item.example_arguments?.[k] ?? "";
      });
      setMcpArgs(args);
    } else {
      setSkillTopic("");
    }
  };

  const closeDrawer = () => {
    setDrawerItem(null);
    setRunResult(null);
  };

  const handleRun = async () => {
    if (!drawerItem) return;
    setRunning(true);
    setRunResult(null);
    try {
      if (drawerKind === "mcp") {
        const tool = drawerItem as MCPTool;
        const res = await fetch(`${API_BASE_URL}/api/v1/ext/mcp/tools/call`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ server: tool.server, tool_name: tool.id, arguments: mcpArgs }),
        });
        setRunResult(await res.json());
      } else {
        const skill = drawerItem as Skill;
        const res = await fetch(`${API_BASE_URL}/api/v1/ext/skills/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            skill_id: skill.id,
            topic: skillTopic,
            subject: skillSubject,
            grade: skillGrade,
          }),
        });
        setRunResult(await res.json());
      }
    } catch (e: any) {
      setRunResult({ status: "error", message: e.message });
    } finally {
      setRunning(false);
    }
  };

  // 发送到工作台对话：写入 localStorage 上下文后跳转
  const sendToWorkbench = () => {
    if (!drawerItem) return;
    const label = drawerItem.name;
    let detail = "";
    if (drawerKind === "mcp") {
      detail = Object.entries(mcpArgs).map(([k, v]) => `${k}: ${v}`).join("；");
    } else {
      detail = skillTopic;
    }
    localStorage.setItem(
      "tool_context",
      JSON.stringify({ kind: drawerKind, name: label, detail })
    );
    window.location.href = "/";
  };

  const runDisabled =
    running ||
    (drawerKind === "mcp"
      ? Object.values(mcpArgs).every((v) => !v?.trim())
      : !skillTopic.trim());

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F7F9FC]">
      {/* Left Sidebar */}
      <aside className="w-72 bg-white border-r border-slate-200/80 flex flex-col h-full shrink-0">
        <div className="p-4 border-b border-slate-100">
          <Link href="/" className="inline-flex items-center space-x-2 text-slate-600 hover:text-blue-600 px-3 py-1.5 rounded-full hover:bg-slate-100 text-xs font-medium transition-colors">
            <ArrowLeft className="w-4 h-4" />
            <span>返回智能体工作台</span>
          </Link>
        </div>

        <div className="p-4 border-b border-slate-100">
          <div className="flex items-center space-x-2.5">
            <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-500 text-white flex items-center justify-center font-bold text-sm shadow-md shadow-blue-500/20">
              <Wrench className="w-4 h-4" />
            </div>
            <div>
              <h2 className="font-bold text-slate-800 text-sm">教学工具中心</h2>
              <p className="text-[10px] text-slate-400">MCP 工具 · 教学技能</p>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          <div className="px-2 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
            快速导航
          </div>
          <Link href="/knowledge" className="flex items-center space-x-2.5 p-3 rounded-[20px] bg-slate-50 hover:bg-blue-50/70 text-slate-700 text-xs font-medium border border-slate-200/70 transition-colors">
            <Database className="w-4 h-4 text-blue-600" />
            <span>知识库与数据处理</span>
          </Link>

          <div className="pt-3 px-2 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
            工具统计
          </div>
          <div className="px-2 space-y-2 text-xs text-slate-500">
            <div className="flex items-center justify-between">
              <span className="flex items-center space-x-2"><Wrench className="w-3.5 h-3.5 text-slate-400" /><span>MCP 工具</span></span>
              <span className="text-slate-700 font-semibold">{mcpTools.length} 个</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="flex items-center space-x-2"><Sparkles className="w-3.5 h-3.5 text-slate-400" /><span>教学技能</span></span>
              <span className="text-slate-700 font-semibold">{skills.length} 个</span>
            </div>
            <div className="flex items-center justify-between">
              <span>我的收藏</span>
              <span className="text-blue-600 font-semibold">{myTools.mcp.length + myTools.skill.length} 个</span>
            </div>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col h-full overflow-y-auto p-8 space-y-6">
        <div className="pb-4 border-b border-slate-200/80">
          <h1 className="text-xl font-bold text-slate-800">教学工具中心</h1>
          <p className="text-xs text-slate-500 mt-1">
            即点即用：MCP 连接外部真实工具，教学技能一键生成教学材料；可试运行、收藏或发送到工作台对话。
          </p>
        </div>

        {/* Tabs */}
        <div className="flex items-center space-x-2">
          <button
            onClick={() => { setActiveTab("mcp"); setActiveCategory("全部"); }}
            className={`px-5 py-2.5 rounded-full text-xs font-medium border transition-all flex items-center space-x-2 ${
              activeTab === "mcp"
                ? "bg-blue-600 text-white border-blue-600 shadow-md shadow-blue-500/20"
                : "bg-white text-slate-600 border-slate-200 hover:border-blue-300"
            }`}
          >
            <Wrench className="w-3.5 h-3.5" />
            <span>MCP 工具广场（{mcpTools.length}）</span>
          </button>
          <button
            onClick={() => { setActiveTab("skill"); setActiveCategory("全部"); }}
            className={`px-5 py-2.5 rounded-full text-xs font-medium border transition-all flex items-center space-x-2 ${
              activeTab === "skill"
                ? "bg-blue-600 text-white border-blue-600 shadow-md shadow-blue-500/20"
                : "bg-white text-slate-600 border-slate-200 hover:border-blue-300"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>教学技能库（{skills.length}）</span>
          </button>
        </div>

        {/* Toolbar: search + categories + only mine */}
        <div className="space-y-3">
          <div className="flex items-center space-x-3">
            <div className="flex-1 flex items-center bg-white border border-slate-200/80 rounded-full px-4 py-2.5 shadow-inner max-w-md">
              <Search className="w-3.5 h-3.5 text-slate-400 mr-2" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索工具名称或用途…"
                className="flex-1 text-xs outline-none bg-transparent text-slate-800"
              />
            </div>
            <button
              onClick={() => setOnlyMine(!onlyMine)}
              className={`px-4 py-2.5 rounded-full text-xs font-medium border transition-all flex items-center space-x-1.5 ${
                onlyMine
                  ? "bg-emerald-50 text-emerald-700 border-emerald-300"
                  : "bg-white text-slate-600 border-slate-200 hover:border-slate-300"
              }`}
            >
              {onlyMine ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
              <span>只看我的</span>
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            {categories.map((c) => (
              <button
                key={c}
                onClick={() => setActiveCategory(c)}
                className={`px-3.5 py-1.5 rounded-full text-[11px] font-medium border transition-all ${
                  activeCategory === c
                    ? "bg-slate-800 text-white border-slate-800"
                    : "bg-white text-slate-600 border-slate-200 hover:border-slate-400"
                }`}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        {/* Card Grid */}
        {filtered.length === 0 ? (
          <div className="bg-white border border-slate-200/80 rounded-[28px] p-12 text-center text-xs text-slate-400">
            {onlyMine ? "还没有收藏，点击卡片右上角 ＋ 即可添加到「我的工具」" : "没有符合条件的工具，换个关键词或分类试试"}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 pb-6">
            {filtered.map((item: any) => {
              const kind: ToolKind = activeTab;
              const mine = isMine(kind, item.id);
              return (
                <div
                  key={item.id}
                  className="bg-white border border-slate-200/80 rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] hover:shadow-md hover:border-blue-200 transition-all flex flex-col"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="w-10 h-10 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0">
                      <Icon name={item.icon} className="w-5 h-5" />
                    </div>
                    <button
                      onClick={() => toggleMine(kind, item.id)}
                      title={mine ? "取消收藏" : "添加到我的工具"}
                      className={`w-7 h-7 rounded-full flex items-center justify-center border transition-all ${
                        mine
                          ? "bg-emerald-500 text-white border-emerald-500"
                          : "bg-white text-slate-400 border-slate-200 hover:text-blue-600 hover:border-blue-300"
                      }`}
                    >
                      {mine ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                  <h3 className="font-semibold text-slate-800 text-sm mb-1">{item.name}</h3>
                  <p className="text-[11px] text-slate-500 leading-relaxed mb-3 flex-1 line-clamp-2">
                    {item.description}
                  </p>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] bg-slate-100 text-slate-500 px-2.5 py-1 rounded-full font-medium">
                      {item.category}
                    </span>
                    <button
                      onClick={() => openDrawer(kind, item)}
                      className="px-3.5 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-full text-[11px] font-medium flex items-center space-x-1 transition-colors"
                    >
                      <span>试运行</span>
                      <ChevronRight className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* Try-run Drawer */}
      {drawerItem && (
        <div className="w-[520px] bg-white border-l border-slate-200 shadow-2xl flex flex-col h-full z-30 md:rounded-l-[32px] overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/60">
            <div className="flex items-center space-x-2.5 min-w-0">
              <div className="w-9 h-9 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0">
                <Icon name={(drawerItem as any).icon} className="w-4.5 h-4.5" />
              </div>
              <div className="min-w-0">
                <h3 className="font-semibold text-slate-800 text-sm truncate">{drawerItem.name}</h3>
                <p className="text-[10px] text-slate-400">
                  {drawerKind === "mcp" ? `MCP · ${(drawerItem as MCPTool).server}` : "教学技能 · 输入主题一键生成"}
                </p>
              </div>
            </div>
            <button onClick={closeDrawer} className="p-2 hover:bg-slate-200/60 rounded-full text-slate-400 hover:text-slate-700 transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            <p className="text-[11px] text-slate-500 leading-relaxed">{(drawerItem as any).description}</p>

            {drawerKind === "mcp" ? (
              <div className="space-y-3">
                {Object.entries((drawerItem as MCPTool).parameters?.properties || {}).map(([key, prop]) => (
                  <div key={key} className="space-y-1.5">
                    <label className="text-[11px] font-medium text-slate-600">
                      {key} <span className="text-slate-400 font-normal">· {prop.description}</span>
                    </label>
                    <textarea
                      value={mcpArgs[key] || ""}
                      onChange={(e) => setMcpArgs({ ...mcpArgs, [key]: e.target.value })}
                      rows={key === "code" ? 5 : 2}
                      className="w-full bg-slate-50 border border-slate-200 rounded-2xl px-3.5 py-2.5 text-xs text-slate-800 outline-none focus:border-blue-500 transition-all font-mono"
                    />
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium text-slate-600">学科</label>
                    <input value={skillSubject} onChange={(e) => setSkillSubject(e.target.value)}
                      className="w-full bg-slate-50 border border-slate-200 rounded-full px-3.5 py-2 text-xs outline-none focus:border-blue-500" />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium text-slate-600">学段</label>
                    <input value={skillGrade} onChange={(e) => setSkillGrade(e.target.value)}
                      className="w-full bg-slate-50 border border-slate-200 rounded-full px-3.5 py-2 text-xs outline-none focus:border-blue-500" />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <label className="text-[11px] font-medium text-slate-600">
                    {(drawerItem as Skill).input_label}
                  </label>
                  <textarea
                    value={skillTopic}
                    onChange={(e) => setSkillTopic(e.target.value)}
                    rows={4}
                    placeholder={(drawerItem as Skill).input_label}
                    className="w-full bg-slate-50 border border-slate-200 rounded-2xl px-3.5 py-2.5 text-xs text-slate-800 outline-none focus:border-blue-500 transition-all"
                  />
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center space-x-2.5 pt-1">
              <button
                onClick={handleRun}
                disabled={runDisabled}
                className="flex-1 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-full text-xs font-medium disabled:opacity-50 transition-all flex items-center justify-center space-x-1.5 shadow-md shadow-blue-500/20"
              >
                {running ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                <span>{running ? "执行中…" : "立即试运行"}</span>
              </button>
              <button
                onClick={sendToWorkbench}
                disabled={runDisabled}
                title="发送到工作台对话"
                className="px-4 py-2.5 bg-white border border-slate-200 hover:border-blue-400 hover:text-blue-600 text-slate-600 rounded-full text-xs font-medium disabled:opacity-50 transition-all flex items-center space-x-1.5"
              >
                <Send className="w-3.5 h-3.5" />
                <span>发送对话</span>
              </button>
            </div>

            {/* Result */}
            {runResult && (
              <div className="border border-slate-200 rounded-[22px] overflow-hidden">
                <div className="px-4 py-2 bg-slate-50 border-b border-slate-100 text-[11px] font-medium text-slate-600 flex items-center space-x-1.5">
                  {runResult.status === "success" ? (
                    <><Check className="w-3.5 h-3.5 text-emerald-600" /><span>执行成功</span></>
                  ) : (
                    <><X className="w-3.5 h-3.5 text-red-500" /><span>执行失败</span></>
                  )}
                </div>
                <div className="p-4">
                  {runResult.status === "success" ? (
                    drawerKind === "skill" && runResult.content ? (
                      <div className="prose prose-sm prose-slate max-w-none text-[12px] leading-relaxed">
                        <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                          {runResult.content}
                        </ReactMarkdown>
                      </div>
                    ) : (
                      <MCPResultView data={runResult} />
                    )
                  ) : (
                    <p className="text-[11px] text-red-600">{runResult.message}</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// MCP 结构化结果渲染：字符串直接展示，数组按条目卡片展示，image_url 显示图片
const MCPResultView: React.FC<{ data: any }> = ({ data }) => {
  const entries = Object.entries(data).filter(([k]) => k !== "status");
  return (
    <div className="space-y-3">
      {entries.map(([key, value]) => {
        if (key === "image_url" && typeof value === "string") {
          return (
            <div key={key} className="space-y-1">
              <span className="text-[10px] text-slate-400 font-medium">{key}</span>
              <img src={value} alt="生成配图" className="w-full rounded-2xl border border-slate-200" />
            </div>
          );
        }
        if (Array.isArray(value)) {
          return (
            <div key={key} className="space-y-1.5">
              <span className="text-[10px] text-slate-400 font-medium">{key}（{value.length}）</span>
              <div className="space-y-1.5">
                {value.map((v: any, i) => (
                  <div key={i} className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-2 text-[11px] text-slate-700">
                    {typeof v === "object"
                      ? Object.entries(v).map(([vk, vv]) => (
                          <div key={vk}><span className="text-slate-400">{vk}: </span>{String(vv)}</div>
                        ))
                      : String(v)}
                  </div>
                ))}
              </div>
            </div>
          );
        }
        return (
          <div key={key} className="text-[11px]">
            <span className="text-slate-400 font-medium">{key}: </span>
            <span className="text-slate-800">{String(value)}</span>
          </div>
        );
      })}
    </div>
  );
};
