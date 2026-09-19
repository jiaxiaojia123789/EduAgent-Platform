"use client";

import React from "react";
import Link from "next/link";
import { Plus, MessageSquare, BookOpen, Settings, LogOut, ChevronRight, Database, Brain, Sparkles, Wrench, ChevronDown } from "lucide-react";
import { useState, useEffect } from "react";
import { AgentInfo, UserInfo, ConversationMeta } from "@/lib/api";

interface AgentSidebarProps {
  agents: AgentInfo[];
  selectedAgentId: string;
  onSelectAgent: (agentId: string) => void;
  onNewChat: () => void;
  currentUser?: UserInfo | null;
  onOpenMemory?: () => void;
  activeMemoryCount?: number;
  onLogout?: () => void;
  isCollapsed?: boolean;
  histories?: ConversationMeta[];
  onSelectHistory?: (conversationId: string) => void;
  activeSessionId?: string | null;
}

export const AgentSidebar: React.FC<AgentSidebarProps> = ({
  agents,
  selectedAgentId,
  onSelectAgent,
  onNewChat,
  currentUser,
  onOpenMemory,
  activeMemoryCount = 4,
  onLogout,
  isCollapsed = false,
  histories = [],
  onSelectHistory,
  activeSessionId = null
}) => {
  const displayUser = currentUser || {
    username: "teacher_demo",
    full_name: "贾老师 (高级教师)",
    role: "teacher"
  };

  const initialLetter = (displayUser.full_name || displayUser.username || "师").charAt(0);

  // 智能体列表默认只展示前 5 个，其余通过「展开其余 N 个」下拉查看
  const PREVIEW_COUNT = 5;
  const [agentsExpanded, setAgentsExpanded] = useState(false);
  const selectedIndex = agents.findIndex((a) => a.id === selectedAgentId);
  // 若当前选中的智能体在折叠区（如刷新后恢复），自动展开保证可见
  const effectiveExpanded = agentsExpanded || selectedIndex >= PREVIEW_COUNT;
  const visibleAgents = effectiveExpanded ? agents : agents.slice(0, PREVIEW_COUNT);
  const hiddenCount = Math.max(0, agents.length - PREVIEW_COUNT);

  // 近期备课历史：默认只显示标签，点击标签下拉展开真实历史数据
  const [historyExpanded, setHistoryExpanded] = useState(false);
  // 切换智能体后自动收起，历史列表随当前智能体变化
  useEffect(() => {
    setHistoryExpanded(false);
  }, [selectedAgentId]);

  /** ISO 时间 → 中文相对时间 */
  const formatHistoryTime = (iso: string): string => {
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

  return (
    <aside className="w-64 bg-slate-50/90 border-r border-slate-200/80 flex flex-col h-full select-none transition-all duration-300 shrink-0">
      {/* Brand Header */}
      <div className="p-4 flex items-center justify-between border-b border-slate-200/60">
        <div className="flex items-center space-x-2.5">
          <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center shadow-md shadow-blue-500/20 text-white font-bold text-base">
            智
          </div>
          <div>
            <h1 className="font-bold text-slate-800 text-sm tracking-tight">智学中台</h1>
            <p className="text-[10px] text-slate-400 font-medium">EduAgent Platform</p>
          </div>
        </div>
      </div>

      {/* Action Buttons: New Chat & Memory Manager */}
      <div className="p-3 space-y-2">
        <button
          onClick={onNewChat}
          className="w-full flex items-center justify-center space-x-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white font-medium py-3 px-4 rounded-full shadow-md shadow-blue-500/20 transition-all duration-150 active:scale-[0.98] text-xs"
        >
          <Plus className="w-4 h-4 stroke-[2.5]" />
          <span>新建对话</span>
        </button>

        {/* Dedicated Memory Management Quick Entry */}
        {onOpenMemory && (
          <button
            onClick={onOpenMemory}
            className="w-full flex items-center justify-between px-3.5 py-2.5 bg-white hover:bg-blue-50/80 border border-slate-200/80 hover:border-blue-300 text-slate-700 hover:text-blue-700 rounded-[18px] transition-all shadow-xs text-xs font-medium group"
          >
            <div className="flex items-center space-x-2">
              <div className="w-6 h-6 rounded-lg bg-blue-50 group-hover:bg-blue-100 flex items-center justify-center text-blue-600 transition-colors">
                <Brain className="w-3.5 h-3.5" />
              </div>
              <span>专属教学记忆</span>
            </div>
            <span className="text-[10px] bg-emerald-50 text-emerald-700 font-semibold px-2 py-0.5 rounded-full border border-emerald-200">
              {activeMemoryCount} 条生效
            </span>
          </button>
        )}
      </div>

      {/* Agent Matrix (Specialized Education Agents) */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-4">
        <div>
          <div className="px-2 mb-1.5 flex items-center justify-between text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
            <span>教育智能体百宝箱</span>
            <span className="bg-blue-100/60 text-blue-700 px-2 py-0.5 rounded-full text-[10px]">
              {agents.length}
            </span>
          </div>

          <div className="space-y-1">
            {visibleAgents.map((agent) => {
              const isSelected = agent.id === selectedAgentId;
              return (
                <button
                  key={agent.id}
                  onClick={() => onSelectAgent(agent.id)}
                  className={`w-full text-left p-2.5 rounded-[18px] transition-all duration-150 flex items-center space-x-2.5 group ${
                    isSelected
                      ? "bg-white text-blue-700 font-medium shadow-sm border border-slate-200/80"
                      : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-900"
                  }`}
                >
                  <span className="text-lg shrink-0 group-hover:scale-110 transition-transform">
                    {agent.avatar}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="text-xs truncate font-bold">{agent.name}</span>
                      <span
                        className={`text-[9px] px-2 py-0.5 rounded-full font-normal ${
                          isSelected
                            ? "bg-blue-50 text-blue-600 border border-blue-200/50"
                            : "bg-slate-100 text-slate-400 group-hover:bg-slate-200/80"
                        }`}
                      >
                        {agent.badge}
                      </span>
                    </div>
                  </div>
                </button>
              );
            })}

            {/* 展开 / 收起开关：超过 5 个智能体时出现 */}
            {hiddenCount > 0 && (
              <button
                onClick={() => setAgentsExpanded(!effectiveExpanded)}
                className="w-full flex items-center justify-center space-x-1 py-2 rounded-[18px] text-[11px] font-medium text-slate-400 hover:text-blue-600 hover:bg-slate-200/50 transition-colors"
              >
                <span>{effectiveExpanded ? "收起" : `展开其余 ${hiddenCount} 个智能体`}</span>
                <ChevronDown
                  className={`w-3.5 h-3.5 transition-transform duration-200 ${effectiveExpanded ? "rotate-180" : ""}`}
                />
              </button>
            )}
          </div>
        </div>

        {/* Recent Session History：标签即下拉键，默认不展示数据 */}
        <div>
          <button
            onClick={() => setHistoryExpanded(!historyExpanded)}
            className="w-full flex items-center justify-between px-2 mb-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider hover:text-slate-600 transition-colors"
            title="展开当前智能体的备课历史"
          >
            <span>近期备课历史{histories.length > 0 ? `（${histories.length}）` : ""}</span>
            <ChevronDown
              className={`w-3.5 h-3.5 transition-transform duration-200 ${historyExpanded ? "rotate-180" : ""}`}
            />
          </button>

          {historyExpanded && (
            <div className="space-y-1">
              {histories.length === 0 ? (
                <p className="px-2.5 py-2 text-[11px] text-slate-400">
                  该智能体暂无备课历史，开始一次对话后会自动保存。
                </p>
              ) : (
                histories.map((h) => {
                  const isCurrent = h.id === activeSessionId;
                  return (
                    <button
                      key={h.id}
                      onClick={() => onSelectHistory?.(h.id)}
                      className={`w-full text-left flex items-start space-x-2 p-2.5 rounded-xl text-xs transition-colors ${
                        isCurrent
                          ? "bg-white text-blue-700 border border-blue-200/60"
                          : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-900"
                      }`}
                    >
                      <MessageSquare className="w-3.5 h-3.5 shrink-0 mt-0.5 text-slate-400" />
                      <div className="flex-1 min-w-0">
                        <div className="truncate">{h.title || "未命名对话"}</div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                          {formatHistoryTime(h.updated_at)} · {h.message_count} 条消息
                          {isCurrent ? " · 当前" : ""}
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>

        {/* Knowledge Base & Tools Links */}
        <div className="pt-2 border-t border-slate-200/60 space-y-2">
          <Link
            href="/knowledge"
            className="flex items-center space-x-2.5 p-3 rounded-[20px] bg-blue-50/60 hover:bg-blue-100/70 text-blue-700 font-medium text-xs border border-blue-200/60 transition-colors shadow-xs"
          >
            <Database className="w-4 h-4 text-blue-600 shrink-0" />
            <div className="flex-1 truncate">
              <div>知识库与数据处理</div>
              <div className="text-[10px] text-blue-500 font-normal">PDF/Word 上传与切片</div>
            </div>
            <ChevronRight className="w-3.5 h-3.5 text-blue-400 shrink-0" />
          </Link>
          <Link
            href="/tools"
            className="flex items-center space-x-2.5 p-3 rounded-[20px] bg-white hover:bg-blue-50/70 text-slate-700 hover:text-blue-700 font-medium text-xs border border-slate-200/80 hover:border-blue-200 transition-colors shadow-xs"
          >
            <Wrench className="w-4 h-4 text-blue-600 shrink-0" />
            <div className="flex-1 truncate">
              <div>教学工具中心</div>
              <div className="text-[10px] text-slate-400 font-normal">MCP 工具 · 教学技能</div>
            </div>
            <ChevronRight className="w-3.5 h-3.5 text-slate-300 shrink-0" />
          </Link>
        </div>
      </div>

      {/* User Footer */}
      <div className="p-3 border-t border-slate-200/60 bg-white/60">
        <div className="flex items-center justify-between p-2 rounded-2xl hover:bg-slate-100/80 transition-colors">
          <div className="flex items-center space-x-2.5 truncate">
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-blue-600 to-indigo-500 text-white flex items-center justify-center font-bold text-xs shrink-0 shadow-xs">
              {initialLetter}
            </div>
            <div className="truncate text-left">
              <div className="text-xs font-semibold text-slate-800 truncate">
                {displayUser.full_name || displayUser.username}
              </div>
              <div className="text-[10px] text-slate-400">
                {displayUser.role === "admin" ? "系统管理员" : "学科教师"} · 已认证
              </div>
            </div>
          </div>
          
          {onLogout ? (
            <button
              onClick={onLogout}
              title="退出当前账户"
              className="p-1.5 hover:bg-red-50 text-slate-400 hover:text-red-600 rounded-full transition-colors"
            >
              <LogOut className="w-4 h-4" />
            </button>
          ) : (
            <Settings className="w-4 h-4 text-slate-400 hover:text-slate-600 shrink-0" />
          )}
        </div>
      </div>
    </aside>
  );
};
