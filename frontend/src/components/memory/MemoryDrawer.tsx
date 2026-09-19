"use client";

import React, { useState, useEffect } from "react";
import { 
  X, Brain, Sparkles, Check, Plus, Trash2, Edit3, 
  Settings, UserCheck, ShieldCheck, RefreshCw, Star, 
  BookOpen, Layers, ToggleLeft, ToggleRight, CheckCircle2
} from "lucide-react";
import { 
  UserProfile, MemoryItem, fetchMemoryProfile, updateMemoryProfile, 
  fetchMemoryItems, createMemoryItem, updateMemoryItem, deleteMemoryItem, 
  reflectMemories 
} from "@/lib/api";

interface MemoryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  onMemoryChanged?: () => void;
  recentMessages?: string[];
}

export const MemoryDrawer: React.FC<MemoryDrawerProps> = ({
  isOpen,
  onClose,
  onMemoryChanged,
  recentMessages = []
}) => {
  const [activeTab, setActiveTab] = useState<"persona" | "memories" | "add" | "reflect">("memories");
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);
  const [successToast, setSuccessToast] = useState("");

  // Add Item State
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newCategory, setNewCategory] = useState("pedagogy");
  const [newImportance, setNewImportance] = useState(4);
  const [addingItem, setAddingItem] = useState(false);

  // Reflection State
  const [reflecting, setReflecting] = useState(false);
  const [reflectedList, setReflectedList] = useState<any[]>([]);

  useEffect(() => {
    if (isOpen) {
      loadData();
    }
  }, [isOpen]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [profData, memData] = await Promise.all([
        fetchMemoryProfile().catch(() => null),
        fetchMemoryItems().catch(() => [])
      ]);
      if (profData) setProfile(profData);
      setMemories(memData);
    } catch (err) {
      console.error("Failed to load memory data:", err);
    } finally {
      setLoading(false);
    }
  };

  const showToast = (msg: string) => {
    setSuccessToast(msg);
    setTimeout(() => setSuccessToast(""), 2500);
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile) return;
    setSavingProfile(true);
    try {
      const updated = await updateMemoryProfile(profile);
      setProfile(updated);
      showToast("✅ 教师教学画像与常驻风格已保存生效！");
      onMemoryChanged?.();
    } catch (err: any) {
      alert("保存画像失败: " + err.message);
    } finally {
      setSavingProfile(false);
    }
  };

  const handleToggleActive = async (item: MemoryItem) => {
    try {
      const nextActive = !item.is_active;
      const updated = await updateMemoryItem(item.id, { is_active: nextActive });
      setMemories(prev => prev.map(m => m.id === item.id ? updated : m));
      showToast(nextActive ? "已激活该记忆规则" : "已停用该记忆（不参与推理）");
      onMemoryChanged?.();
    } catch (err: any) {
      alert("切换状态失败: " + err.message);
    }
  };

  const handleDeleteItem = async (itemId: string) => {
    if (!confirm("确定要删除这条教学记忆吗？")) return;
    try {
      await deleteMemoryItem(itemId);
      setMemories(prev => prev.filter(m => m.id !== itemId));
      showToast("已成功删除记忆");
      onMemoryChanged?.();
    } catch (err: any) {
      alert("删除失败: " + err.message);
    }
  };

  const handleAddItem = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim() || !newContent.trim()) return;
    setAddingItem(true);
    try {
      const created = await createMemoryItem({
        title: newTitle.trim(),
        content: newContent.trim(),
        category: newCategory,
        importance: newImportance,
        is_active: true
      });
      setMemories(prev => [created, ...prev]);
      setNewTitle("");
      setNewContent("");
      setActiveTab("memories");
      showToast("🎉 新教学记忆已添加并生效！");
      onMemoryChanged?.();
    } catch (err: any) {
      alert("添加记忆失败: " + err.message);
    } finally {
      setAddingItem(false);
    }
  };

  const handleTriggerReflect = async () => {
    setReflecting(true);
    try {
      const dialogues = recentMessages.length > 0 ? recentMessages : [
        "老师，我们在讲导数极值点时，学生总是不记得验证导数两边的符号变化。",
        "公开课板书必须左侧定理核心式、右侧草稿演练，而且必须有易错题变式梯级设计。"
      ];
      const res = await reflectMemories(dialogues);
      setReflectedList(res.reflected_items || []);
      showToast(`已成功提炼出 ${res.reflected_items?.length || 0} 项潜在教学习惯！`);
    } catch (err: any) {
      alert("反思失败: " + err.message);
    } finally {
      setReflecting(false);
    }
  };

  const handleAdoptReflected = async (refItem: any) => {
    try {
      const created = await createMemoryItem({
        title: refItem.title,
        content: refItem.content,
        category: refItem.category,
        importance: refItem.importance,
        is_active: true
      });
      setMemories(prev => [created, ...prev]);
      setReflectedList(prev => prev.filter(r => r.title !== refItem.title));
      showToast(`已采纳《${refItem.title}》至生效记忆库！`);
      onMemoryChanged?.();
    } catch (err: any) {
      alert("采纳失败: " + err.message);
    }
  };

  if (!isOpen) return null;

  const activeCount = memories.filter(m => m.is_active).length;

  const getCategoryBadge = (cat: string) => {
    switch (cat) {
      case "pedagogy":
        return <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-[10px] font-medium border border-blue-200/60">教学法理念</span>;
      case "preference":
        return <span className="bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full text-[10px] font-medium border border-purple-200/60">板书与题型偏好</span>;
      case "student_status":
        return <span className="bg-amber-50 text-amber-800 px-2 py-0.5 rounded-full text-[10px] font-medium border border-amber-200/60">班级学情诊断</span>;
      default:
        return <span className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded-full text-[10px] font-medium">自定义备课经验</span>;
    }
  };

  return (
    <div className="w-[520px] lg:w-[580px] bg-white border-l border-slate-200/80 shadow-2xl flex flex-col h-full z-40 animate-in slide-in-from-right duration-200 md:rounded-l-[32px] overflow-hidden select-none">
      {/* Header */}
      <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
        <div className="flex items-center space-x-2.5">
          <div className="w-10 h-10 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-600 text-white flex items-center justify-center shadow-md shadow-blue-500/20">
            <Brain className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="font-bold text-slate-800 text-sm">教师专属教学记忆中心</h3>
              <span className="text-[10px] bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full font-medium border border-emerald-200">
                {activeCount} 条生效中
              </span>
            </div>
            <p className="text-[11px] text-slate-400">千人千师 · 注入 8 大教育专家智能体推理底座</p>
          </div>
        </div>

        <button
          onClick={onClose}
          className="p-2 hover:bg-slate-200/60 rounded-full text-slate-400 hover:text-slate-700 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Success Toast */}
      {successToast && (
        <div className="mx-4 mt-3 p-2.5 bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-2xl text-xs flex items-center space-x-2 animate-in fade-in duration-150">
          <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
          <span>{successToast}</span>
        </div>
      )}

      {/* Pill Tabs */}
      <div className="px-4 pt-3">
        <div className="flex bg-slate-100/90 p-1 rounded-full text-xs font-medium text-slate-500">
          <button
            onClick={() => setActiveTab("memories")}
            className={`flex-1 py-1.5 rounded-full transition-all flex items-center justify-center space-x-1 ${
              activeTab === "memories" ? "bg-white text-blue-700 shadow-sm font-semibold" : "hover:text-slate-800"
            }`}
          >
            <span>生效记忆库</span>
            <span className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.2 rounded-full font-normal">
              {memories.length}
            </span>
          </button>
          <button
            onClick={() => setActiveTab("persona")}
            className={`flex-1 py-1.5 rounded-full transition-all ${
              activeTab === "persona" ? "bg-white text-blue-700 shadow-sm font-semibold" : "hover:text-slate-800"
            }`}
          >
            教学画像与学情
          </button>
          <button
            onClick={() => setActiveTab("add")}
            className={`flex-1 py-1.5 rounded-full transition-all ${
              activeTab === "add" ? "bg-white text-blue-700 shadow-sm font-semibold" : "hover:text-slate-800"
            }`}
          >
            + 录入新记忆
          </button>
          <button
            onClick={() => setActiveTab("reflect")}
            className={`flex-1 py-1.5 rounded-full transition-all flex items-center justify-center space-x-1 ${
              activeTab === "reflect" ? "bg-white text-blue-700 shadow-sm font-semibold" : "hover:text-slate-800"
            }`}
          >
            <Sparkles className="w-3 h-3 text-amber-500" />
            <span>AI 反思提炼</span>
          </button>
        </div>
      </div>

      {/* Tab Contents */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
        {loading ? (
          <div className="h-64 flex flex-col items-center justify-center text-slate-400 space-y-2">
            <RefreshCw className="w-5 h-5 animate-spin text-blue-600" />
            <span>正在加载教师个性化记忆体系...</span>
          </div>
        ) : (
          <>
            {/* 1. Active Memories Tab */}
            {activeTab === "memories" && (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-[11px] text-slate-400 px-1">
                  <span>以下规则已实时挂载至教案生成、试卷命制与智能答疑：</span>
                  <span className="text-blue-600 font-medium">{activeCount} 项开启</span>
                </div>

                {memories.length === 0 ? (
                  <div className="p-8 text-center text-slate-400 bg-slate-50/60 rounded-[24px] border border-dashed border-slate-200">
                    暂无记忆条目，可切换至【+ 录入新记忆】或【AI 反思提炼】添加
                  </div>
                ) : (
                  memories.map((mem) => (
                    <div
                      key={mem.id}
                      className={`p-4 rounded-[22px] border transition-all space-y-2 ${
                        mem.is_active
                          ? "bg-white border-slate-200/90 shadow-sm"
                          : "bg-slate-50/70 border-slate-200/50 opacity-60"
                      }`}
                    >
                      <div className="flex items-start justify-between">
                        <div className="space-y-1">
                          <div className="flex items-center space-x-2">
                            {getCategoryBadge(mem.category)}
                            <h4 className="font-semibold text-slate-800 text-xs">{mem.title}</h4>
                          </div>
                          <div className="flex items-center space-x-1 text-[10px] text-amber-500">
                            <span>重要度:</span>
                            {Array.from({ length: mem.importance }).map((_, i) => (
                              <Star key={i} className="w-3 h-3 fill-amber-400 stroke-amber-400" />
                            ))}
                          </div>
                        </div>

                        <div className="flex items-center space-x-2">
                          <button
                            type="button"
                            onClick={() => handleToggleActive(mem)}
                            title={mem.is_active ? "点击停用" : "点击激活"}
                            className="text-slate-400 hover:text-blue-600 transition-colors"
                          >
                            {mem.is_active ? (
                              <ToggleRight className="w-6 h-6 text-blue-600 fill-blue-100" />
                            ) : (
                              <ToggleLeft className="w-6 h-6 text-slate-300" />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteItem(mem.id)}
                            className="p-1 hover:bg-red-50 text-slate-400 hover:text-red-600 rounded-full transition-colors"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <p className="text-slate-600 text-xs leading-relaxed bg-slate-50/80 p-2.5 rounded-xl border border-slate-100">
                        {mem.content}
                      </p>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* 2. Persona Profile Tab */}
            {activeTab === "persona" && profile && (
              <form onSubmit={handleSaveProfile} className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-slate-600 font-medium px-1">主教学科</label>
                    <input
                      type="text"
                      value={profile.subject}
                      onChange={(e) => setProfile({ ...profile, subject: e.target.value })}
                      required
                      className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-slate-600 font-medium px-1">授课学段</label>
                    <input
                      type="text"
                      value={profile.grade}
                      onChange={(e) => setProfile({ ...profile, grade: e.target.value })}
                      required
                      className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800"
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-slate-600 font-medium px-1">教材版本</label>
                  <input
                    type="text"
                    value={profile.textbook_version}
                    onChange={(e) => setProfile({ ...profile, textbook_version: e.target.value })}
                    required
                    className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-slate-600 font-medium px-1">授课班级学情诊断与薄弱环节</label>
                  <textarea
                    rows={3}
                    value={profile.student_analysis}
                    onChange={(e) => setProfile({ ...profile, student_analysis: e.target.value })}
                    className="w-full p-3 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800 leading-relaxed"
                    placeholder="描述所带班级学生的数学基础、常见解题误区或易错思维点"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-slate-600 font-medium px-1">常驻教学法风格与板书习惯</label>
                  <textarea
                    rows={3}
                    value={profile.teaching_style}
                    onChange={(e) => setProfile({ ...profile, teaching_style: e.target.value })}
                    className="w-full p-3 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800 leading-relaxed"
                    placeholder="如：启发式问题链教学法、三分区板书、强调数形结合"
                  />
                </div>

                <button
                  type="submit"
                  disabled={savingProfile}
                  className="w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white font-medium rounded-full shadow-md shadow-blue-500/20 flex items-center justify-center space-x-1.5 transition-all disabled:opacity-50"
                >
                  {savingProfile ? <RefreshCw className="w-4 h-4 animate-spin mr-1" /> : <Check className="w-4 h-4 mr-1" />}
                  <span>保存教学画像与风格配置</span>
                </button>
              </form>
            )}

            {/* 3. Add New Memory Tab */}
            {activeTab === "add" && (
              <form onSubmit={handleAddItem} className="space-y-3.5">
                <div className="space-y-1">
                  <label className="text-slate-600 font-medium px-1">记忆偏好标题 / 简要标签</label>
                  <input
                    type="text"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    required
                    placeholder="如：立体几何引入坚持实物模型投影"
                    className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-slate-600 font-medium px-1">记忆类别</label>
                    <select
                      value={newCategory}
                      onChange={(e) => setNewCategory(e.target.value)}
                      className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800"
                    >
                      <option value="pedagogy">教学法与理念</option>
                      <option value="preference">板书与题型偏好</option>
                      <option value="student_status">班级学情诊断</option>
                      <option value="custom">自定义备课经验</option>
                    </select>
                  </div>
                  <div className="space-y-1">
                    <label className="text-slate-600 font-medium px-1">重要程度评星</label>
                    <select
                      value={newImportance}
                      onChange={(e) => setNewImportance(Number(e.target.value))}
                      className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800"
                    >
                      <option value={5}>★★★★★ (最高优先级强制遵循)</option>
                      <option value={4}>★★★★☆ (重要备课规范)</option>
                      <option value={3}>★★★☆☆ (一般习惯推荐)</option>
                      <option value={2}>★★☆☆☆ (轻度参考)</option>
                    </select>
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-slate-600 font-medium px-1">记忆详细说明与约束要求</label>
                  <textarea
                    rows={4}
                    value={newContent}
                    onChange={(e) => setNewContent(e.target.value)}
                    required
                    placeholder="具体描述该教学习惯或约束规则，例如：在命制空间向量试题时，必须同时给出几何法与建系代数法两种参考解答，便于不同层次学生参考。"
                    className="w-full p-3 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:border-blue-500 focus:bg-white text-slate-800 leading-relaxed"
                  />
                </div>

                <button
                  type="submit"
                  disabled={addingItem}
                  className="w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white font-medium rounded-full shadow-md shadow-blue-500/20 flex items-center justify-center space-x-1.5 transition-all disabled:opacity-50"
                >
                  {addingItem ? <RefreshCw className="w-4 h-4 animate-spin mr-1" /> : <Plus className="w-4 h-4 mr-1" />}
                  <span>确认添加到专属生效记忆库</span>
                </button>
              </form>
            )}

            {/* 4. AI Reflection Tab */}
            {activeTab === "reflect" && (
              <div className="space-y-3.5">
                <div className="p-4 bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200/70 rounded-[24px] space-y-2">
                  <div className="flex items-center space-x-2 text-blue-900 font-bold">
                    <Sparkles className="w-4 h-4 text-amber-500" />
                    <span>智能体交互记忆自动提炼</span>
                  </div>
                  <p className="text-[11px] text-blue-800/80 leading-relaxed">
                    AI 将深度研读教师与多智能体的历史交流记录，自动反思并提炼出教师潜在的授课偏好、板书习惯或学情关注点。
                  </p>
                  <button
                    onClick={handleTriggerReflect}
                    disabled={reflecting}
                    className="mt-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-full text-xs flex items-center space-x-1.5 shadow-sm transition-all disabled:opacity-50"
                  >
                    {reflecting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                    <span>开始分析近期会话提炼新偏好</span>
                  </button>
                </div>

                {reflectedList.length > 0 && (
                  <div className="space-y-2.5">
                    <div className="text-[11px] font-semibold text-slate-500 px-1">
                      提炼候选记忆条目 ({reflectedList.length})：
                    </div>
                    {reflectedList.map((item, idx) => (
                      <div key={idx} className="p-3.5 bg-white border border-slate-200/80 rounded-[22px] space-y-2 shadow-xs">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center space-x-2">
                            {getCategoryBadge(item.category)}
                            <span className="font-semibold text-slate-800">{item.title}</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleAdoptReflected(item)}
                            className="px-3 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded-full text-[11px] font-medium flex items-center space-x-1 shadow-xs transition-colors"
                          >
                            <Check className="w-3 h-3" />
                            <span>采纳此记忆</span>
                          </button>
                        </div>
                        <p className="text-slate-600 text-xs bg-slate-50 p-2.5 rounded-xl border border-slate-100">
                          {item.content}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {/* Footer info */}
      <div className="p-3 border-t border-slate-100 bg-slate-50/60 flex items-center justify-between text-[11px] text-slate-400">
        <span>记忆隔离存储 · 重启持久化保存</span>
        <span>已联动 8 大学科专家智能体</span>
      </div>
    </div>
  );
};
