"use client";

import React, { useState, useEffect, useRef } from "react";
import { Send, Paperclip, Sparkles, LayoutPanelLeft, RefreshCw, Eye, BookOpen, Layers, Brain, ChevronDown, Trash2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/**
 * 预处理 LLM 输出的 markdown：
 * 1. 统一 \r\n 为 \n
 * 2. 去除每行尾部空格
 * 3. 去除标题行前的前导空格（LLM 常输出 " ## 标题"）
 * 4. 规范化列表标记："*   " → "- "（GFM 兼容）
 * 5. 确保标题/列表/代码块前后有空行
 */
function preprocessMarkdown(raw: string): string {
  if (!raw) return "";
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const result: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    let line = lines[i].replace(/\s+$/, ""); // 去行尾空格

    // 标题行：去除前导空格
    if (/^\s+#{1,6}\s/.test(line)) {
      line = line.replace(/^\s+/, "");
    }
    // 列表项："*   text" → "- text"，统一用 - 标记
    if (/^\s*\*\s+/.test(line)) {
      line = line.replace(/^(\s*)\*\s+/, "$1- ");
    }
    result.push(line);
  }
  return result.join("\n");
}

/**
 * 将后端 trace 事件（{step_id?, node_name?, phase?, action, title, elapsed_ms?}）
 * 归并为前端 ThinkingAccordion 需要的 TraceStep：
 * - 有 step_id：TOOL_RESULT_DONE 等终态事件覆盖更新进行中的同名步骤；
 * - 无 step_id：用 node/phase + title 合成稳定 key，避免重复插入。
 */
function mergeTraceStep(steps: TraceStep[], p: any): TraceStep[] {
  const stepId = p.step_id || `${p.node_name || p.phase || "agent"}::${p.title || ""}`;
  const next: TraceStep = {
    step_id: stepId,
    node_name: p.node_name || p.phase || "Supervisor",
    action_type: p.action || "THINKING",
    title: p.title || "",
    elapsed_ms: p.elapsed_ms
  };
  const idx = steps.findIndex((s) => s.step_id === stepId);
  if (idx === -1) return [...steps, next];
  const merged = [...steps];
  merged[idx] = { ...steps[idx], ...next };
  return merged;
}
import { AgentSidebar } from "@/components/sidebar/AgentSidebar";
import { ThinkingAccordion } from "@/components/chat/ThinkingAccordion";
import { CitationPopover } from "@/components/chat/CitationPopover";
import { ArtifactCanvas } from "@/components/canvas/ArtifactCanvas";
import { MemoryDrawer } from "@/components/memory/MemoryDrawer";
import {
  fetchAgentMatrix, runAgentStream, AgentInfo, MessageItem, TraceStep,
  getAuthToken, getCurrentUser, clearAuthToken, getStoredUser,
  fetchMemoryItems, UserInfo, PlanDAG, SubAgentResultSchema,
  ConversationMeta, StoredMessage,
  fetchConversations, fetchConversationDetail, deleteConversation
} from "@/lib/api";

export default function DoubaoEducationalWorkspace() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>("supervisor");
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeArtifact, setActiveArtifact] = useState<any>(null);
  const [isCanvasOpen, setIsCanvasOpen] = useState(false);
  const [activeKbId, setActiveKbId] = useState<string>("kb-math-01");
  const [uploadingDoc, setUploadingDoc] = useState<boolean>(false);

  // 当前流式任务的中断控制器（新建对话时中止旧流）
  const streamAbortRef = useRef<AbortController | null>(null);

  // 历史对话状态
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [historiesByAgent, setHistoriesByAgent] = useState<Record<string, ConversationMeta[]>>({});
  const [historyOpen, setHistoryOpen] = useState(false);

  // User Auth & Memory State
  const [currentUser, setCurrentUser] = useState<UserInfo | null>(null);
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const [activeMemoryCount, setActiveMemoryCount] = useState(4);

  const chatBottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const historyContainerRef = useRef<HTMLDivElement>(null);

  /** 根据 agent 信息构造一条专属欢迎消息 */
  const buildGreetingMessage = (agent: AgentInfo | undefined): MessageItem => {
    return {
      id: `greeting-${agent?.id || "unknown"}-${Date.now()}`,
      role: "assistant",
      agent_name: agent?.name || "智学中台",
      agent_avatar: agent?.avatar || "🧭",
      content: agent?.greeting || "您好，请问有什么可以帮您？",
      timestamp: "刚刚"
    };
  };

  /** ISO 时间 → 中文相对时间 */
  const formatRelativeTime = (iso: string): string => {
    const then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    const diffSec = Math.floor((Date.now() - then) / 1000);
    if (diffSec < 60) return "刚刚";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
    if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} 天前`;
    const d = new Date(then);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  };

  /** 加载并缓存某 agent 的历史对话列表 */
  const loadHistory = async (agentId: string) => {
    try {
      const list = await fetchConversations(agentId, currentUser?.id || "u-001");
      setHistoriesByAgent((prev) => ({ ...prev, [agentId]: list }));
      return list;
    } catch (e) {
      console.error("加载历史对话失败:", e);
      return [];
    }
  };

  /** 存储的消息 → 前端消息项 */
  const mapStoredMessage = (m: StoredMessage): MessageItem => {
    const extra = m.extra || {};
    return {
      id: m.id,
      role: m.role as "user" | "assistant",
      content: m.content,
      agent_name: extra.agent_name,
      agent_avatar: extra.agent_avatar,
      citations: m.citations || [],
      artifact: extra.artifact,
      plan_dag: extra.plan_dag || null,
      sub_results: extra.sub_results || [],
      timestamp: new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    };
  };

  /** 打开并恢复某条历史对话 */
  const handleOpenConversation = async (conversationId: string) => {
    // 切换到历史会话：中断可能仍在进行的流式任务
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setLoading(false);
    try {
      const { messages: stored } = await fetchConversationDetail(conversationId);
      const restored = stored
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map(mapStoredMessage);
      setMessages(restored);
      setActiveSessionId(conversationId);
      setHistoryOpen(false);
      // 恢复最近一个 assistant 消息的协同视窗产物
      const lastWithArtifact = [...restored].reverse().find((m) => m.artifact);
      if (lastWithArtifact?.artifact) {
        setActiveArtifact(lastWithArtifact.artifact);
        setIsCanvasOpen(true);
      } else {
        setActiveArtifact(null);
        setIsCanvasOpen(false);
      }
      localStorage.setItem(`last_session:${selectedAgentId}`, conversationId);
    } catch (e) {
      console.error(e);
    }
  };

  /** 删除一条历史对话；若正在查看该对话则重置为新窗口 */
  const handleDeleteHistory = async (conversationId: string) => {
    try {
      await deleteConversation(conversationId);
      const list = await loadHistory(selectedAgentId);
      if (activeSessionId === conversationId) {
        const agent = agents.find((a) => a.id === selectedAgentId);
        setMessages([buildGreetingMessage(agent)]);
        setActiveSessionId(null);
        setActiveArtifact(null);
        setIsCanvasOpen(false);
      }
      localStorage.removeItem(`last_session:${selectedAgentId}`);
      if (list.length === 0) setHistoryOpen(false);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    // 1. Auth check
    const token = getAuthToken();
    if (!token) {
      window.location.href = "/login";
      return;
    }

    // Load current user profile
    const stored = getStoredUser();
    if (stored) setCurrentUser(stored);

    getCurrentUser().then(user => {
      setCurrentUser(user);
    }).catch(() => {
      // If token invalid, redirect to login
      clearAuthToken();
      window.location.href = "/login";
    });

    // 2. Load agent matrix
    async function loadAgents() {
      const data = await fetchAgentMatrix();
      setAgents(data);
      const supervisor = data.find((a: AgentInfo) => a.id === "supervisor");

      // 加载总控历史列表
      let supervisorHistory: ConversationMeta[] = [];
      try {
        supervisorHistory = await fetchConversations("supervisor");
        setHistoriesByAgent((prev) => ({ ...prev, supervisor: supervisorHistory }));
      } catch {
        // 历史加载失败时降级为欢迎词
      }

      // 登录进入后：始终进入中台总控的「全新对话」，只显示专属欢迎词，
      // 不恢复/自动打开任何历史对话（历史仍可从顶部下拉手动打开）

      // 默认：总控专属欢迎词
      if (supervisor) {
        setMessages([
          {
            id: "welcome",
            role: "assistant",
            agent_name: supervisor.name,
            agent_avatar: supervisor.avatar,
            content: supervisor.greeting,
            timestamp: "刚刚"
          }
        ]);
      }
    }
    loadAgents();

    // 3. Load active memory count
    fetchMemoryItems(true).then(items => {
      setActiveMemoryCount(items.length);
    }).catch(() => {});

    // 4. 从教学工具中心带入的工具上下文：预填到输入框
    try {
      const rawTool = localStorage.getItem("tool_context");
      if (rawTool) {
        const ctx = JSON.parse(rawTool);
        const detailLine = ctx.detail ? `（${ctx.detail}）` : "";
        setInputValue(`请使用【${ctx.name}】${detailLine}帮我完成这项教学任务。`);
        localStorage.removeItem("tool_context");
      }
    } catch {}
  }, []);

  // 流式期间消息高频更新：瞬时滚动避免 smooth 动画队列堆积卡死主线程；
  // 非流式（历史恢复/发送瞬间）仍用平滑滚动
  const isStreaming = messages.some((m) => m.streaming);
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: isStreaming ? "auto" : "smooth" });
  }, [messages, loading, isStreaming]);

  // 历史下拉打开时：点击面板外部或按 Escape 关闭
  useEffect(() => {
    if (!historyOpen) return;
    const handlePointerDown = (e: MouseEvent) => {
      if (
        historyContainerRef.current &&
        !historyContainerRef.current.contains(e.target as Node)
      ) {
        setHistoryOpen(false);
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHistoryOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [historyOpen]);

  const handleLogout = () => {
    // 清除所有智能体的"上次停留会话"记忆，
    // 保证重新登录后进入中台总控的最新对话，而不是退出时停留的旧页面
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith("last_session:"))
        .forEach((k) => localStorage.removeItem(k));
    } catch {}
    clearAuthToken();
    window.location.href = "/login";
  };

  /**
   * 切换 agent：跳转到该 agent 的全新对话窗口
   * 清空历史消息，只显示对应 agent 的专属欢迎词，关闭旧协同视窗，
   * 并加载该 agent 的历史对话列表（不在窗口中直接恢复）
   */
  const handleSelectAgent = (id: string) => {
    // 切换智能体：中断可能仍在进行的流式任务
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setLoading(false);
    setSelectedAgentId(id);
    setHistoryOpen(false);
    const agent = agents.find((a) => a.id === id);
    setMessages([buildGreetingMessage(agent)]);
    setActiveSessionId(null);
    setActiveArtifact(null);
    setIsCanvasOpen(false);
    // 懒加载该 agent 的历史列表
    if (!historiesByAgent[id]) {
      loadHistory(id);
    }
  };

  /** 当前 agent 下新建对话：旧对话已实时落库，窗口重置为专属欢迎词 */
  const handleNewChat = () => {
    // 中断可能仍在进行的流式任务，避免旧 token 写入新对话窗口
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setLoading(false);
    setMessages([buildGreetingMessage(agents.find((a) => a.id === selectedAgentId))]);
    setActiveSessionId(null);
    setActiveArtifact(null);
    setIsCanvasOpen(false);
    setHistoryOpen(false);
    localStorage.removeItem(`last_session:${selectedAgentId}`);
    loadHistory(selectedAgentId);
  };

  const refreshMemoryCount = async () => {
    try {
      const items = await fetchMemoryItems(true);
      setActiveMemoryCount(items.length);
    } catch (e) {
      console.error(e);
    }
  };

  const currentAgent = agents.find((a) => a.id === selectedAgentId) || {
    id: "supervisor",
    name: "中台总控智能体",
    avatar: "🧭",
    badge: "智能中枢",
    description: "智能识别教学需求意图，自动分流至最适合的学科专家智能体",
    sample_prompts: ["请为《导数的几何意义》设计一份45分钟精品公开课教案"]
  };

  const handleSendMessage = async (textToSend?: string) => {
    const text = textToSend || inputValue;
    if (!text.trim() || loading) return;

    const nowTs = Date.now();
    const userMsg: MessageItem = {
      id: String(nowTs),
      role: "user",
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    };
    // assistant 占位消息：SSE 期间始终更新同一条消息（同引用），实现打字机效果
    const assistantId = `stream-${nowTs + 1}`;
    const assistantMsg: MessageItem = {
      id: assistantId,
      role: "assistant",
      agent_name: currentAgent.name,
      agent_avatar: currentAgent.avatar,
      content: "",
      steps: [],
      citations: [],
      streaming: true,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInputValue("");
    setLoading(true);

    // 局部补丁流式占位消息，避免整体重建消息列表
    const patchAssistant = (patch: Partial<MessageItem>) => {
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m)));
    };
    let streamedArtifact: any = null;

    // token 缓冲：LLM 分片粒度很细，逐片 setState 会让 ReactMarkdown/KaTeX
    // 全量重解析数百次而饿死中间帧渲染。按 80ms 节拍批量 flush，兼顾流畅与实时感。
    let tokenBuffer = "";
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    const flushTokens = () => {
      flushTimer = null;
      if (!tokenBuffer) return;
      const chunk = tokenBuffer;
      tokenBuffer = "";
      setMessages((prev) => prev.map((m) =>
        m.id === assistantId ? { ...m, content: m.content + chunk } : m
      ));
    };
    const clearFlushTimer = () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
    };

    const abortController = new AbortController();
    streamAbortRef.current?.abort();
    streamAbortRef.current = abortController;

    try {
      await runAgentStream(
        {
          message: text,
          agentType: selectedAgentId,
          kbIds: [activeKbId],
          userId: currentUser?.id || "u-001",
          subAgentMode: false,
          conversationId: activeSessionId,
          signal: abortController.signal
        },
        {
          onTrace: (payload) => {
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, steps: mergeTraceStep(m.steps || [], payload) } : m
            ));
          },
          onToken: (chunk) => {
            // 入缓冲，按固定节拍批量追加（同一条消息，ReactMarkdown 增量重渲染）
            tokenBuffer += chunk;
            if (flushTimer === null) {
              flushTimer = setTimeout(flushTokens, 80);
            }
          },
          onArtifact: (artifact) => {
            streamedArtifact = artifact;
            patchAssistant({ artifact });
          },
          onDone: (payload) => {
            // 先排空剩余 token，再以 done 权威结果一次性定稿，避免尾部截断
            clearFlushTimer();
            flushTokens();
            const finalArtifact = payload.artifact || streamedArtifact || undefined;
            patchAssistant({
              content: payload.output || "生成完成",
              steps: (payload.trace_summary && payload.trace_summary.steps) || undefined,
              citations: payload.citations || [],
              artifact: finalArtifact,
              plan_dag: payload.plan_dag || null,
              sub_results: payload.sub_results || [],
              streaming: false
            });

            // 绑定后端落库的历史对话 ID
            if (payload.conversation_id) {
              setActiveSessionId(payload.conversation_id);
              localStorage.setItem(`last_session:${selectedAgentId}`, payload.conversation_id);
            }
            if (finalArtifact) {
              setActiveArtifact(finalArtifact);
              setIsCanvasOpen(true);
            }
            // 刷新当前 agent 历史列表（标题/排序/消息数）
            loadHistory(selectedAgentId);
          },
          onError: (message) => {
            clearFlushTimer();
            tokenBuffer = "";
            patchAssistant({
              content: `抱歉，${message}`,
              streaming: false
            });
            loadHistory(selectedAgentId);
          }
        }
      );
    } catch (err: any) {
      // 切换/新建对话触发的主动中止：静默处理，不污染新对话
      if (err?.name === "AbortError") {
        clearFlushTimer();
        return;
      }
      console.error("Agent stream failed:", err);
      clearFlushTimer();
      patchAssistant({
        content: `抱歉，任务执行失败：${err?.message || "网络或配置异常，请检查后台连接或重试。"}`,
        streaming: false
      });
    } finally {
      clearFlushTimer();
      if (streamAbortRef.current === abortController) {
        streamAbortRef.current = null;
      }
      setLoading(false);
    }
  };

  const handleFileUploadChat = async (file: File) => {
    if (!file) return;
    setUploadingDoc(true);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("kb_id", activeKbId);
    formData.append("subject", "高中数学");
    formData.append("grade", "高二");

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001"}/api/v1/knowledge/upload`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("上传失败");
      const data = await res.json();

      const notifyMsg: MessageItem = {
        id: String(Date.now()),
        role: "assistant",
        agent_name: "RAG 数据处理中枢",
        agent_avatar: "📚",
        content: `📄 已成功解析并导入《${data.filename}》（文档格式: ${data.doc_type}，生成 ${data.chunks_created} 个语义切片，包含 ${data.formula_count} 处公式、${data.table_count} 个表格）至知识库！\n\n已自动挂载至当前 RAG 检索上下文。您可以直接针对该文档向我提问、要求生成教案或命制考题。`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      };
      setMessages((prev) => [...prev, notifyMsg]);
    } catch (e: any) {
      alert("上传文档失败: " + e.message);
    } finally {
      setUploadingDoc(false);
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F7F9FC]">
      {/* 1. Left Sidebar (Agent Matrix & History & Memory Entry) */}
      <AgentSidebar
        agents={agents}
        selectedAgentId={selectedAgentId}
        onSelectAgent={handleSelectAgent}
        onNewChat={handleNewChat}
        currentUser={currentUser}
        onOpenMemory={() => setIsMemoryOpen(true)}
        activeMemoryCount={activeMemoryCount}
        onLogout={handleLogout}
        histories={historiesByAgent[selectedAgentId] || []}
        onSelectHistory={handleOpenConversation}
        activeSessionId={activeSessionId}
      />

      {/* 2. Main Center Chat Column (Doubao Style) */}
      <main className="flex-1 flex flex-col h-full overflow-hidden relative">
        {/* 对话页品牌背景：低透明度水印式主视觉，边缘径向柔化；
            pointer-events-none + z-0 保证不拦截交互、不遮挡对话 */}
        <div
          aria-hidden="true"
          className="absolute inset-0 z-0 pointer-events-none opacity-[0.14]"
          style={{
            backgroundImage: "url('/content.png')",
            backgroundRepeat: "no-repeat",
            backgroundPosition: "center 56%",
            backgroundSize: "min(78%, 760px)",
            WebkitMaskImage: "radial-gradient(ellipse at center, black 45%, transparent 72%)",
            maskImage: "radial-gradient(ellipse at center, black 45%, transparent 72%)"
          }}
        />

        {/* Top Header */}
        <header className="h-14 border-b border-slate-200/80 bg-white/80 backdrop-blur-md px-6 flex items-center justify-between shrink-0 z-10">
          <div ref={historyContainerRef} className="flex items-center space-x-3 relative">
            <span className="text-xl">{currentAgent.avatar}</span>
            <div>
              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => setHistoryOpen((v) => !v)}
                  className="flex items-center space-x-1 group"
                  title="展开本智能体历史对话"
                >
                  <span className="font-semibold text-slate-800 text-sm group-hover:text-blue-600 transition-colors">
                    {currentAgent.name}
                  </span>
                  <ChevronDown
                    className={`w-3.5 h-3.5 text-slate-400 group-hover:text-blue-500 transition-transform ${
                      historyOpen ? "rotate-180" : ""
                    }`}
                  />
                </button>
                <span className="text-[10px] bg-blue-50 text-blue-600 font-medium px-2 py-0.5 rounded-full border border-blue-200/60">
                  {currentAgent.badge}
                </span>
                <button
                  type="button"
                  onClick={handleNewChat}
                  title="在本智能体下新建对话"
                  className="ml-1 flex items-center space-x-1 text-[10px] text-slate-500 hover:text-blue-600 px-2 py-0.5 rounded-full border border-slate-200 hover:border-blue-300 hover:bg-blue-50/60 transition-all"
                >
                  <span>新建对话</span>
                </button>
              </div>
              <p className="text-[11px] text-slate-400 truncate max-w-sm">
                {currentAgent.description}
              </p>
            </div>

            {/* 历史对话下拉面板：只显示当前 agent 的对话 */}
            {historyOpen && (
              <div className="absolute top-full left-0 mt-2 w-[340px] bg-white rounded-2xl border border-slate-200 shadow-[0_18px_48px_-12px_rgba(0,0,0,0.18)] z-50 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-100 bg-slate-50/60">
                  <span className="text-[11px] font-semibold text-slate-600">
                    {currentAgent.avatar} {currentAgent.name} · 历史对话
                  </span>
                  <span className="text-[10px] text-slate-400">
                    {(historiesByAgent[selectedAgentId] || []).length} 条
                  </span>
                </div>
                <div className="max-h-[320px] overflow-y-auto py-1">
                  {(historiesByAgent[selectedAgentId] || []).length === 0 ? (
                    <div className="px-4 py-8 text-center text-[11px] text-slate-400">
                      暂无历史对话，开始你的第一次提问吧
                    </div>
                  ) : (
                    historiesByAgent[selectedAgentId].map((c) => (
                      <div
                        key={c.id}
                        className={`group flex items-center justify-between px-4 py-2.5 hover:bg-blue-50/60 cursor-pointer transition-colors ${
                          activeSessionId === c.id ? "bg-blue-50/80" : ""
                        }`}
                        onClick={() => handleOpenConversation(c.id)}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center space-x-1.5">
                            <span className="text-[12px] text-slate-700 font-medium truncate">
                              {c.title}
                            </span>
                            {activeSessionId === c.id && (
                              <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-blue-500" />
                            )}
                          </div>
                          <div className="text-[10px] text-slate-400 mt-0.5">
                            {formatRelativeTime(c.updated_at)} · {c.message_count} 条消息
                          </div>
                        </div>
                        <button
                          type="button"
                          title="删除该对话"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (window.confirm(`确定删除对话「${c.title}」吗？此操作不可恢复。`)) {
                              handleDeleteHistory(c.id);
                            }
                          }}
                          className="shrink-0 ml-2 w-6 h-6 rounded-full flex items-center justify-center text-slate-400 hover:text-red-500 hover:bg-red-50 opacity-60 group-hover:opacity-100 transition-all"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center space-x-2.5 text-xs text-slate-500">
            {/* Memory Center Trigger Pill */}
            <button
              onClick={() => setIsMemoryOpen(true)}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-white hover:bg-blue-50/80 border border-slate-200/80 hover:border-blue-300 rounded-full text-slate-700 shadow-xs transition-all text-xs font-medium"
            >
              <Brain className="w-3.5 h-3.5 text-blue-600" />
              <span>教学记忆</span>
              <span className="text-[10px] bg-emerald-50 text-emerald-700 px-1.5 py-0.2 rounded-full font-semibold border border-emerald-200">
                {activeMemoryCount} 条生效
              </span>
            </button>

            <div className="flex items-center space-x-1.5 bg-slate-100 px-3 py-1 rounded-full text-[11px] text-slate-600 border border-slate-200/50">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              <span>阿里百炼 Qwen-Max 在线</span>
            </div>

            {activeArtifact && (
              <button
                onClick={() => setIsCanvasOpen(!isCanvasOpen)}
                className="flex items-center space-x-1.5 px-3.5 py-1.5 bg-white border border-slate-200 hover:bg-slate-50 rounded-full text-slate-700 shadow-xs transition-all text-xs font-medium"
              >
                <Layers className="w-3.5 h-3.5 text-blue-600" />
                <span>{isCanvasOpen ? "隐藏协同视窗" : "展开协同视窗"}</span>
              </button>
            )}
          </div>
        </header>

        {/* Chat Feed */}
        <div className="relative z-10 flex-1 overflow-y-auto p-4 md:p-8 space-y-6 max-w-4xl mx-auto w-full">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex items-start space-x-3 ${
                msg.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              {msg.role === "assistant" && (
                <div className="w-9 h-9 rounded-2xl bg-white border border-slate-200/80 shadow-xs flex items-center justify-center text-lg shrink-0 mt-0.5">
                  {msg.agent_avatar || "🤖"}
                </div>
              )}

              <div
                className={`text-xs md:text-sm leading-relaxed transition-all ${
                  msg.role === "user"
                    ? "max-w-[82%] md:max-w-[72%] rounded-[24px] rounded-br-[6px] px-5 py-3.5 bg-gradient-to-r from-blue-600 to-blue-500 text-white shadow-[0_4px_18px_rgba(37,99,235,0.22)]"
                    : "max-w-[88%] md:max-w-[80%] rounded-[26px] rounded-bl-[6px] p-5 bg-white text-slate-800 border border-slate-100 shadow-[0_6px_24px_-4px_rgba(0,0,0,0.05)]"
                }`}
              >
                {/* Collapsible Deep Thinking Accordion */}
                {msg.role === "assistant" && msg.steps && msg.steps.length > 0 && (
                  <ThinkingAccordion steps={msg.steps} />
                )}

                {/* Plan DAG: 任务分解可视化 */}
                {msg.role === "assistant" && msg.plan_dag && msg.plan_dag.nodes && msg.plan_dag.nodes.length > 0 && (
                  <div className="mb-3 p-3 bg-slate-50/80 rounded-xl border border-slate-200/60">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[11px] font-semibold text-slate-600 flex items-center">
                        <Layers className="w-3 h-3 mr-1" />
                        任务分解 DAG
                      </span>
                      <span className="text-[10px] bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full border border-blue-200/60">
                        {msg.plan_dag.schedule} · {msg.plan_dag.total_tasks} 个子任务
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {msg.plan_dag.nodes.map((node) => (
                        <div key={node.task_id} className="flex items-center space-x-2 text-[11px]">
                          <span className="shrink-0 w-4 text-center">
                            {node.status === "DONE" ? "✅" : node.status === "RUNNING" ? "🔄" : node.status === "FAILED" ? "❌" : "⏳"}
                          </span>
                          <span className="shrink-0 bg-slate-200/60 text-slate-700 px-1.5 py-0.5 rounded">
                            {node.task_id}
                          </span>
                          <span className="shrink-0 text-blue-600 font-medium min-w-[80px]">
                            {node.agent}
                          </span>
                          <span className="flex-1 text-slate-500 truncate">
                            {node.input_summary}
                          </span>
                          {node.depends_on && node.depends_on.length > 0 && (
                            <span className="shrink-0 text-slate-400 text-[10px]">
                              ← {node.depends_on.join(", ")}
                            </span>
                          )}
                          {node.map_count > 1 && (
                            <span className="shrink-0 text-[10px] bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded-full border border-purple-200/60">
                              x{node.map_count}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                    {/* Sub-agent 结果摘要 */}
                    {msg.sub_results && msg.sub_results.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-slate-200/60 text-[10px] text-slate-400">
                        {msg.sub_results.filter(r => r.status === "FAILED").length > 0 ? (
                          <span className="text-red-500">
                            ⚠️ {msg.sub_results.filter(r => r.status === "FAILED").length} 个子任务失败
                          </span>
                        ) : (
                          <span>
                            ✅ 全部 {msg.sub_results.length} 个 sub-agent 执行完成
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Main Message Content - 流式思考态 / Markdown + LaTeX 渲染 */}
                {msg.role === "assistant" && msg.streaming && !msg.content && (!msg.steps || msg.steps.length === 0) ? (
                  <div className="flex items-center space-x-2 text-xs text-slate-500 py-1">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin text-blue-600" />
                    <span>正在结合专属教学记忆，协同调用学科专家智能体与 RAG 检索...</span>
                  </div>
                ) : (
                  <div className="prose prose-slate prose-sm max-w-none
                    prose-headings:font-semibold prose-headings:text-slate-800
                    prose-h1:text-xl prose-h1:mt-4 prose-h1:mb-2 prose-h1:border-b prose-h1:border-slate-200 prose-h1:pb-1
                    prose-h2:text-lg prose-h2:mt-3 prose-h2:mb-2 prose-h2:text-blue-700
                    prose-h3:text-base prose-h3:mt-2 prose-h3:mb-1 prose-h3:text-slate-700
                    prose-p:my-1.5 prose-p:leading-relaxed
                    prose-ul:my-2 prose-ul:list-disc prose-ul:pl-5
                    prose-ol:my-2 prose-ol:list-decimal prose-ol:pl-5
                    prose-li:my-0.5
                    prose-strong:font-semibold prose-strong:text-slate-900
                    prose-blockquote:border-l-4 prose-blockquote:border-blue-300 prose-blockquote:bg-blue-50/50 prose-blockquote:py-1 prose-blockquote:pl-3 prose-blockquote:my-2 prose-blockquote:rounded-r
                    prose-code:text-pink-600 prose-code:bg-pink-50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[13px] prose-code:before:content-none prose-code:after:content-none
                    prose-pre:bg-slate-800 prose-pre:text-slate-100 prose-pre:rounded-lg prose-pre:p-3 prose-pre:overflow-x-auto
                    prose-table:border-collapse prose-table:my-3 prose-table:w-full
                    prose-th:border prose-th:border-slate-300 prose-th:bg-slate-100 prose-th:px-3 prose-th:py-1.5 prose-th:text-left prose-th:font-semibold prose-th:text-sm
                    prose-td:border prose-td:border-slate-300 prose-td:px-3 prose-td:py-1.5 prose-td:text-sm
                    prose-hr:border-slate-200 prose-hr:my-4
                    [&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex]:text-base
                  ">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[rehypeKatex]}
                    >
                      {preprocessMarkdown(msg.content)}
                    </ReactMarkdown>
                    {msg.streaming && (
                      <span className="inline-block w-[3px] h-4 ml-1 align-middle bg-blue-500 animate-pulse rounded-sm" />
                    )}
                  </div>
                )}

                {/* Citations Preview */}
                {msg.citations && msg.citations.length > 0 && (
                  <div className="mt-3 pt-2.5 border-t border-slate-100 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                    <span className="text-[11px] font-medium text-slate-400 flex items-center mr-1">
                      <BookOpen className="w-3 h-3 mr-1" />
                      引用文献：
                    </span>
                    {msg.citations.map((c) => (
                      <CitationPopover key={c.citation_id} citation={c} />
                    ))}
                  </div>
                )}

                {/* Artifact Action Pill */}
                {msg.artifact && (
                  <div className="mt-3 pt-2.5 border-t border-slate-100 flex items-center justify-between">
                    <span className="text-[11px] text-emerald-600 font-medium">
                      已在右侧生成标准化教学成果
                    </span>
                    <button
                      onClick={() => {
                        setActiveArtifact(msg.artifact);
                        setIsCanvasOpen(true);
                      }}
                      className="inline-flex items-center space-x-1 text-xs text-blue-600 hover:text-blue-700 font-medium px-2.5 py-1 rounded-full hover:bg-blue-50 transition-colors"
                    >
                      <Eye className="w-3.5 h-3.5" />
                      <span>查看成果视窗</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Loading Indicator：流式期间由 assistant 占位气泡内的思考态/打字光标承载，此处不再重复渲染 */}

          <div ref={chatBottomRef} />
        </div>

        {/* Bottom Floating Prompt Area (Doubao Style) */}
        <div className="relative z-10 p-4 max-w-4xl mx-auto w-full shrink-0">
          {/* Quick Action Suggestion Pills */}
          <div className="flex items-center space-x-2 mb-2.5 overflow-x-auto pb-1 text-xs no-scrollbar">
            {currentAgent.sample_prompts.map((prompt, idx) => (
              <button
                key={idx}
                onClick={() => handleSendMessage(prompt)}
                className="shrink-0 bg-white/95 hover:bg-blue-50/80 border border-slate-200/70 text-slate-600 hover:text-blue-700 px-4 py-1.5 rounded-full shadow-[0_2px_8px_rgba(0,0,0,0.03)] transition-all flex items-center space-x-1.5 text-[11px]"
              >
                <Sparkles className="w-3 h-3 text-blue-500" />
                <span>{prompt}</span>
              </button>
            ))}
          </div>

          {/* Capsule Input Bar (Smooth Doubao Style) */}
          <div className="relative bg-white/95 backdrop-blur-xl border border-slate-200/80 rounded-[32px] shadow-[0_10px_35px_-6px_rgba(0,0,0,0.07)] p-2 pl-3 flex items-center space-x-2 focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-100/60 transition-all duration-200">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.doc,.md,.txt"
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.length) {
                  handleFileUploadChat(e.target.files[0]);
                }
              }}
            />

            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingDoc}
              title="上传课件、教材PDF或Word文档至RAG知识库"
              className="w-8 h-8 rounded-full hover:bg-slate-100 text-slate-400 hover:text-blue-600 flex items-center justify-center transition-colors disabled:opacity-50"
            >
              {uploadingDoc ? (
                <RefreshCw className="w-4 h-4 animate-spin text-blue-600" />
              ) : (
                <Paperclip className="w-4 h-4" />
              )}
            </button>

            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSendMessage();
                }
              }}
              placeholder={`向 ${currentAgent.name} 提问或提出教学任务指令...`}
              className="flex-1 bg-transparent border-none outline-none text-xs md:text-sm text-slate-800 placeholder-slate-400 px-2"
            />

            <button
              type="button"
              onClick={() => handleSendMessage()}
              disabled={!inputValue.trim() || loading}
              className="w-9 h-9 bg-gradient-to-tr from-blue-600 to-indigo-500 hover:from-blue-700 hover:to-indigo-600 disabled:opacity-40 text-white rounded-full shadow-md shadow-blue-500/25 flex items-center justify-center transition-all active:scale-95 shrink-0"
            >
              <Send className="w-4 h-4 ml-0.5" />
            </button>
          </div>
          <div className="text-center mt-2 text-[10px] text-slate-400">
            智学中台已挂载教师专属教学记忆 · 采用 Agent Harness 护栏与 MinerU 混合检索
          </div>
        </div>

      </main>

      {/* 3. Right Slide-out Artifact Canvas */}
      <ArtifactCanvas
        isOpen={isCanvasOpen}
        onClose={() => setIsCanvasOpen(false)}
        artifact={activeArtifact}
      />

      {/* 4. Right Slide-out Memory Management Drawer */}
      <MemoryDrawer
        isOpen={isMemoryOpen}
        onClose={() => setIsMemoryOpen(false)}
        onMemoryChanged={refreshMemoryCount}
        recentMessages={messages.map(m => m.content)}
      />
    </div>
  );
}
