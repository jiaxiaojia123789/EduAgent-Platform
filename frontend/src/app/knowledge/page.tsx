"use client";

import React, { useState, useEffect, useRef } from "react";
import { 
  Upload, FileText, FileSpreadsheet, Database, Search, Trash2, 
  Eye, CheckCircle2, AlertCircle, ArrowLeft, Plus, RefreshCw,
  Sparkles, Layers, BookOpen, Clock, Tag
} from "lucide-react";
import Link from "next/link";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

interface KnowledgeBase {
  id: string;
  name: string;
  subject: string;
  grade: string;
  description: string;
  document_count: number;
  chunk_count: number;
}

interface DocumentItem {
  id: string;
  kb_id: string;
  filename: string;
  doc_type: string;
  file_size: number;
  chunk_count: number;
  formula_count: number;
  table_count: number;
  status: string;
  created_at: string;
}

interface ChunkItem {
  chunk_id: string;
  content: string;
  tokens: number;
  formula_count: number;
  table_count: number;
  page_number: number;
  metadata: {
    section_path?: string;
    has_formula?: boolean;
    has_table?: boolean;
  };
}

type DocType = "PDF" | "WORD" | "MARKDOWN" | "TXT";

interface StrategyMeta {
  id: string;
  name: string;
  description: string;
  default_chunk_size: number | null;
  default_overlap: number | null;
}

interface StrategiesMeta {
  strategies: StrategyMeta[];
  constraints: {
    chunk_size_range: [number, number];
    chunk_overlap_max_ratio: string;
    default_overlap_ratio: string;
  };
  auto_mapping: Record<DocType, { strategy: string; chunk_size: number; overlap: number }>;
}

interface PreviewResult {
  doc_type: string;
  chunk_strategy: string;
  chunk_size: number;
  chunk_overlap: number;
  total_chunks: number;
  token_stats: { min: number; max: number; avg: number };
  preview_chunks: {
    chunk_index: number;
    content: string;
    tokens: number;
    formula_count: number;
    table_count: number;
    section_path: string;
  }[];
}

const DOC_TYPE_LABELS: Record<DocType, string> = {
  PDF: "PDF 文档",
  WORD: "Word 文档",
  MARKDOWN: "Markdown",
  TXT: "纯文本",
};

const detectDocType = (filename: string): DocType => {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (ext === "pdf") return "PDF";
  if (ext === "docx" || ext === "doc") return "WORD";
  if (ext === "md" || ext === "markdown") return "MARKDOWN";
  return "TXT";
};

export default function KnowledgeBasePage() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>("kb-math-01");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(false);
  
  // Upload State
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStep, setUploadStep] = useState<string>("");
  const [uploadResult, setUploadResult] = useState<any>(null);

  // Chunking Config State (动态分块策略配置)
  const [strategiesMeta, setStrategiesMeta] = useState<StrategiesMeta | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [docType, setDocType] = useState<DocType>("PDF");
  const [typeManuallySet, setTypeManuallySet] = useState(false);
  const [chunkStrategy, setChunkStrategy] = useState("auto");
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(64);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);

  // Chunk Preview Drawer
  const [previewChunks, setPreviewChunks] = useState<ChunkItem[]>([]);
  const [previewDocName, setPreviewDocName] = useState<string>("");
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);

  // Search Playground
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  // Drag and Drop
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch KBs on mount
  useEffect(() => {
    fetchKBs();
    fetchStrategiesMeta();
  }, []);

  // Fetch documents when selected KB changes
  useEffect(() => {
    if (selectedKbId) {
      fetchDocuments(selectedKbId);
    }
  }, [selectedKbId]);

  const fetchKBs = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/list`);
      const data = await res.json();
      setKbs(data.knowledge_bases || []);
      if (data.knowledge_bases?.length > 0 && !selectedKbId) {
        setSelectedKbId(data.knowledge_bases[0].id);
      }
    } catch (e) {
      console.error("Failed to load KBs:", e);
    }
  };

  const fetchDocuments = async (kbId: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/${kbId}/documents`);
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch (e) {
      console.error("Failed to load documents:", e);
    } finally {
      setLoading(false);
    }
  };

  const fetchStrategiesMeta = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/chunk-strategies`);
      const data: StrategiesMeta = await res.json();
      setStrategiesMeta(data);
    } catch (e) {
      console.error("Failed to load chunk strategies:", e);
    }
  };

  // 按文档类型应用推荐策略与默认参数
  const applyRecommendation = (type: DocType) => {
    const rec = strategiesMeta?.auto_mapping?.[type];
    if (!rec) return;
    setChunkStrategy(rec.strategy);
    setChunkSize(rec.chunk_size);
    setChunkOverlap(rec.overlap);
  };

  // 选中文件：自动识别类型并联动推荐（不直接上传）
  const handleFileSelect = (file: File) => {
    if (!file) return;
    const type = detectDocType(file.name);
    setSelectedFile(file);
    setDocType(type);
    setTypeManuallySet(false);
    setPreviewResult(null);
    setUploadResult(null);
    setShowAdvanced(false);
    applyRecommendation(type);
  };

  // 手动切换文档类型：重置为该类型推荐配置
  const handleTypeChange = (type: DocType) => {
    setDocType(type);
    setTypeManuallySet(true);
    setPreviewResult(null);
    applyRecommendation(type);
  };

  // 切换分块策略：重置为该策略默认参数
  const handleStrategyChange = (sid: string) => {
    setChunkStrategy(sid);
    setPreviewResult(null);
    if (sid === "auto") {
      applyRecommendation(docType);
      return;
    }
    const meta = strategiesMeta?.strategies.find((s) => s.id === sid);
    if (meta) {
      const size = meta.default_chunk_size ?? 512;
      const overlap = meta.default_overlap ?? Math.floor(size / 8);
      setChunkSize(size);
      setChunkOverlap(overlap);
    }
  };

  // chunk_size 变化：overlap 超过 50% 时自动夹取
  const handleChunkSizeChange = (size: number) => {
    setChunkSize(size);
    setPreviewResult(null);
    if (chunkOverlap > Math.floor(size / 2)) {
      setChunkOverlap(Math.floor(size / 2));
    }
  };

  const buildChunkFormParams = (formData: FormData) => {
    if (chunkStrategy === "auto") {
      formData.append("chunk_strategy", "auto");
      formData.append("chunk_size", "0");
      formData.append("chunk_overlap", "0");
    } else {
      formData.append("chunk_strategy", chunkStrategy);
      formData.append("chunk_size", String(chunkSize));
      formData.append("chunk_overlap", String(chunkOverlap));
    }
  };

  // Dry-run 分块预览：不入库
  const handlePreviewChunking = async () => {
    if (!selectedFile) return;
    setIsPreviewing(true);
    setPreviewResult(null);
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      buildChunkFormParams(formData);
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/chunk-preview`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "预览失败");
      }
      setPreviewResult(await res.json());
    } catch (err: any) {
      alert(`分块预览失败: ${err.message}`);
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    setIsUploading(true);
    setUploadResult(null);
    setUploadStep("1. 正在安全上传文件并验证格式...");

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("kb_id", selectedKbId);
    formData.append("subject", currentKb?.subject || "高中数学");
    formData.append("grade", currentKb?.grade || "高二");
    buildChunkFormParams(formData);

    const strategyLabel = chunkStrategy === "auto"
      ? `自动 (${docType})`
      : strategiesMeta?.strategies.find((s) => s.id === chunkStrategy)?.name || chunkStrategy;
    setTimeout(() => setUploadStep("2. 执行 MinerU / Docx 版面分析，提取表格与 LaTeX 公式..."), 800);
    setTimeout(() => setUploadStep(`3. 正在按「${strategyLabel}」策略分块 (${chunkStrategy === "auto" ? "推荐参数" : `${chunkSize} token + ${chunkOverlap} overlap`})...`), 1600);
    setTimeout(() => setUploadStep("4. 正在调用阿里百炼 text-embedding-v3 进行向量嵌入并写入 Milvus 2.4..."), 2400);

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/upload`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "上传失败");
      }

      const data = await res.json();
      setUploadResult(data);
      setUploadStep("✅ 文档已完成全链路解析并成功入库 Milvus 2.4 与 BM25！");
      // Refresh documents and KBs
      setSelectedFile(null);
      setPreviewResult(null);
      fetchDocuments(selectedKbId);
      fetchKBs();
    } catch (err: any) {
      alert(`上传解析失败: ${err.message}`);
      setUploadStep("");
    } finally {
      setIsUploading(false);
    }
  };

  const cancelUploadConfig = () => {
    setSelectedFile(null);
    setPreviewResult(null);
  };

  const handleInspectChunks = async (docId: string, filename: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents/${docId}/chunks`);
      const data = await res.json();
      setPreviewChunks(data.chunks || []);
      setPreviewDocName(filename);
      setIsPreviewOpen(true);
    } catch (e) {
      alert("获取切片失败");
    }
  };

  const handleDeleteDocument = async (docId: string) => {
    if (!confirm("确定要从知识库中删除该文档及其所有向量切片吗？")) return;

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents/${docId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        fetchDocuments(selectedKbId);
        fetchKBs();
      }
    } catch (e) {
      alert("删除失败");
    }
  };

  const handleSearchPlayground = async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/knowledge/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: searchQuery,
          kb_ids: [selectedKbId],
          top_k: 3,
          enable_rerank: true
        })
      });
      const data = await res.json();
      setSearchResults(data.results || []);
    } catch (e) {
      console.error(e);
    } finally {
      setIsSearching(false);
    }
  };

  const currentKb = kbs.find(k => k.id === selectedKbId);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F7F9FC]">
      {/* 1. Left Sidebar: Knowledge Bases */}
      <aside className="w-72 bg-white border-r border-slate-200/80 flex flex-col h-full shrink-0">
        <div className="p-4 border-b border-slate-100 flex items-center justify-between">
          <Link href="/" className="inline-flex items-center space-x-2 text-slate-600 hover:text-blue-600 px-3 py-1.5 rounded-full hover:bg-slate-100 text-xs font-medium transition-colors">
            <ArrowLeft className="w-4 h-4" />
            <span>返回智能体工作台</span>
          </Link>
        </div>

        <div className="p-4 border-b border-slate-100">
          <div className="flex items-center space-x-2.5">
            <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-500 text-white flex items-center justify-center font-bold text-sm shadow-md shadow-blue-500/20">
              <Database className="w-4 h-4" />
            </div>
            <div>
              <h2 className="font-bold text-slate-800 text-sm">RAG 知识库中心</h2>
              <p className="text-[10px] text-slate-400">Milvus 2.4 + MinerU 解析</p>
            </div>
          </div>
        </div>

        {/* KB List */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          <div className="px-2 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
            已有知识库 ({kbs.length})
          </div>
          {kbs.map((kb) => {
            const isSelected = kb.id === selectedKbId;
            return (
              <button
                key={kb.id}
                onClick={() => setSelectedKbId(kb.id)}
                className={`w-full text-left p-3.5 rounded-[22px] transition-all border ${
                  isSelected
                    ? "bg-blue-50/70 border-blue-200 text-blue-900 shadow-xs"
                    : "bg-white border-slate-200/70 hover:bg-slate-50 text-slate-700"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-xs truncate max-w-[140px]">{kb.name}</span>
                  <span className="text-[10px] bg-white px-2.5 py-0.5 rounded-full font-medium border border-slate-200 text-slate-600">
                    {kb.subject}
                  </span>
                </div>
                <p className="text-[11px] text-slate-500 line-clamp-2 leading-relaxed mb-2">
                  {kb.description}
                </p>
                <div className="flex items-center space-x-3 text-[10px] text-slate-400">
                  <span>📄 {kb.document_count} 个文档</span>
                  <span>🧩 {kb.chunk_count} 个切片</span>
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      {/* 2. Main Content Area */}
      <main className="flex-1 flex flex-col h-full overflow-y-auto p-8 space-y-6">
        {/* KB Header */}
        <div className="flex items-center justify-between pb-4 border-b border-slate-200/80">
          <div>
            <div className="flex items-center space-x-3">
              <h1 className="text-xl font-bold text-slate-800">{currentKb?.name}</h1>
              <span className="text-xs bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-full border border-emerald-200 font-medium">
                Milvus 2.4 HNSW 索引正常
              </span>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              适用学段: {currentKb?.grade} · 嵌入模型: 阿里百炼 text-embedding-v3 (1024维) · 检索模式: BM25 + 向量混合 (RRF)
            </p>
          </div>
        </div>

        {/* Upload Zone (Drag & Drop) */}
        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            if (e.dataTransfer.files?.length > 0) {
              handleFileSelect(e.dataTransfer.files[0]);
            }
          }}
          onClick={() => fileInputRef.current?.click()}
          className={`border-2 border-dashed rounded-[36px] p-9 text-center cursor-pointer transition-all shadow-[0_4px_20px_rgba(0,0,0,0.02)] ${
            isDragging
              ? "border-blue-500 bg-blue-50/60 scale-[0.99]"
              : "border-slate-300 hover:border-blue-400 bg-white shadow-sm"
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.doc,.md,.txt"
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) {
                handleFileSelect(e.target.files[0]);
              }
            }}
          />
          <div className="w-14 h-14 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center mx-auto mb-3 shadow-xs">
            <Upload className="w-6 h-6" />
          </div>
          <h3 className="font-semibold text-slate-800 text-sm">
            点击或将 PDF、Word (.docx)、Markdown 文档拖拽至此处
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            选择文档类型后自动匹配最佳分块策略，支持 Dry-run 预览切片效果，确认后入库 Milvus 与 BM25
          </p>
          <div className="flex items-center justify-center space-x-2 mt-3 text-[11px] text-slate-500">
            <span className="bg-slate-100/90 text-slate-600 px-3 py-1 rounded-full font-medium border border-slate-200/50">PDF (.pdf)</span>
            <span className="bg-slate-100/90 text-slate-600 px-3 py-1 rounded-full font-medium border border-slate-200/50">Word (.docx)</span>
            <span className="bg-slate-100/90 text-slate-600 px-3 py-1 rounded-full font-medium border border-slate-200/50">Markdown (.md)</span>
            <span className="bg-slate-100/90 text-slate-600 px-3 py-1 rounded-full font-medium border border-slate-200/50">文本 (.txt)</span>
          </div>
        </div>

        {/* Upload Config Panel (动态分块策略配置) */}
        {selectedFile && (
          <div className="bg-white border border-blue-200/80 rounded-[28px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.03)] space-y-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2.5 min-w-0">
                <Layers className="w-4 h-4 text-blue-600 shrink-0" />
                <h3 className="font-semibold text-slate-800 text-sm shrink-0">分块策略配置</h3>
                <span className="text-xs text-slate-400 truncate">{selectedFile.name}</span>
              </div>
              <button
                onClick={cancelUploadConfig}
                className="p-1.5 hover:bg-slate-100 rounded-full text-slate-400 hover:text-slate-600 transition-colors shrink-0"
              >
                ✕
              </button>
            </div>

            {/* 1. 文档类型（自动识别 + 可手动改） */}
            <div className="space-y-2">
              <div className="flex items-center space-x-2 text-xs">
                <span className="font-medium text-slate-600">1. 文档类型</span>
                {!typeManuallySet && (
                  <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-[10px] border border-blue-200">
                    已按扩展名自动识别
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {(["PDF", "WORD", "MARKDOWN", "TXT"] as DocType[]).map((t) => (
                  <button
                    key={t}
                    onClick={() => handleTypeChange(t)}
                    className={`px-4 py-1.5 rounded-full text-xs font-medium border transition-all ${
                      docType === t
                        ? "bg-blue-600 text-white border-blue-600 shadow-sm shadow-blue-500/20"
                        : "bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600"
                    }`}
                  >
                    {DOC_TYPE_LABELS[t]}
                  </button>
                ))}
              </div>
            </div>

            {/* 2. 分块策略（联动推荐） */}
            <div className="space-y-2">
              <span className="font-medium text-slate-600 text-xs">2. 分块策略</span>
              <div className="flex items-start space-x-3">
                <select
                  value={chunkStrategy}
                  onChange={(e) => handleStrategyChange(e.target.value)}
                  className="bg-slate-50/80 border border-slate-200/80 rounded-full px-4 py-2 text-xs text-slate-800 outline-none focus:border-blue-500 transition-all shrink-0"
                >
                  <option value="auto">自动选择（按文档类型推荐）</option>
                  {strategiesMeta?.strategies.filter((s) => s.id !== "auto").map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
                {chunkStrategy !== "auto" && (
                  <p className="text-[11px] text-slate-400 flex-1 leading-relaxed pt-1.5">
                    {strategiesMeta?.strategies.find((s) => s.id === chunkStrategy)?.description}
                  </p>
                )}
              </div>
              {chunkStrategy === "auto" && strategiesMeta?.auto_mapping?.[docType] && (
                <p className="text-[11px] text-slate-500 bg-slate-50 border border-slate-100 rounded-2xl px-3.5 py-2 leading-relaxed">
                  当前类型 <b>{DOC_TYPE_LABELS[docType]}</b> 推荐策略：
                  <b className="text-blue-600">
                    {" "}{strategiesMeta.strategies.find((s) => s.id === strategiesMeta.auto_mapping[docType].strategy)?.name}{" "}
                  </b>
                  （chunk_size {strategiesMeta.auto_mapping[docType].chunk_size} / overlap {strategiesMeta.auto_mapping[docType].overlap}），上传时自动应用
                </p>
              )}
            </div>

            {/* 3. 高级参数（auto 模式下锁定为推荐值） */}
            <div className="space-y-3">
              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center space-x-1.5 text-xs font-medium text-slate-600 hover:text-blue-600 transition-colors"
              >
                <span>3. 高级参数（chunk_size / overlap）</span>
                <span className="text-[10px]">{showAdvanced ? "收起 ▲" : "展开 ▼"}</span>
              </button>
              {showAdvanced && (
                <div className={`grid grid-cols-1 md:grid-cols-2 gap-5 ${chunkStrategy === "auto" ? "opacity-50 pointer-events-none" : ""}`}>
                  <div className="space-y-2">
                    <div className="flex justify-between text-[11px] text-slate-500">
                      <span>分块大小 chunk_size</span>
                      <span className="font-semibold text-slate-700">{chunkSize} tokens</span>
                    </div>
                    <input
                      type="range"
                      min={128}
                      max={1024}
                      step={32}
                      value={chunkSize}
                      onChange={(e) => handleChunkSizeChange(Number(e.target.value))}
                      className="w-full accent-blue-600"
                    />
                    <p className="text-[10px] text-slate-400">允许范围 128 - 1024，越界将被拒绝</p>
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between text-[11px] text-slate-500">
                      <span>重叠长度 chunk_overlap</span>
                      <span className="font-semibold text-slate-700">
                        {chunkOverlap} tokens（{Math.round((chunkOverlap / chunkSize) * 100)}%）
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={Math.floor(chunkSize / 2)}
                      step={8}
                      value={chunkOverlap}
                      onChange={(e) => { setChunkOverlap(Number(e.target.value)); setPreviewResult(null); }}
                      className="w-full accent-blue-600"
                    />
                    <p className="text-[10px] text-slate-400">不超过 chunk_size 的 50%，超出自动截断</p>
                  </div>
                </div>
              )}
            </div>

            {/* 操作区 */}
            <div className="flex flex-wrap items-center gap-2.5 pt-1">
              <button
                onClick={handlePreviewChunking}
                disabled={isPreviewing || isUploading}
                className="px-5 py-2.5 bg-white border border-slate-200 hover:border-blue-400 hover:text-blue-600 text-slate-700 rounded-full text-xs font-medium disabled:opacity-50 transition-all flex items-center space-x-1.5"
              >
                {isPreviewing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
                <span>预览分块效果（Dry-run）</span>
              </button>
              <button
                onClick={handleUpload}
                disabled={isUploading}
                className="px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-full text-xs font-medium disabled:opacity-50 transition-all flex items-center space-x-1.5 shadow-md shadow-blue-500/20 active:scale-95"
              >
                {isUploading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                <span>确认上传入库</span>
              </button>
            </div>

            {/* Dry-run 预览结果 */}
            {previewResult && (
              <div className="bg-slate-50/80 border border-slate-200/70 rounded-[22px] p-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="bg-blue-100/70 text-blue-800 px-2.5 py-0.5 rounded-full font-medium">
                    实际策略: {previewResult.chunk_strategy}
                  </span>
                  <span className="bg-white border border-slate-200 text-slate-600 px-2.5 py-0.5 rounded-full">
                    chunk_size {previewResult.chunk_size} / overlap {previewResult.chunk_overlap}
                  </span>
                  <span className="bg-white border border-slate-200 text-slate-600 px-2.5 py-0.5 rounded-full">
                    共 {previewResult.total_chunks} 个切片
                  </span>
                  <span className="bg-white border border-slate-200 text-slate-600 px-2.5 py-0.5 rounded-full">
                    tokens {previewResult.token_stats.min}~{previewResult.token_stats.max}（均值 {previewResult.token_stats.avg}）
                  </span>
                </div>
                <div className="space-y-2">
                  {previewResult.preview_chunks.map((c) => (
                    <div key={c.chunk_index} className="bg-white border border-slate-100 rounded-2xl p-3 space-y-1.5">
                      <div className="flex items-center justify-between text-[10px] text-slate-400">
                        <span className="font-semibold text-blue-600">切片 #{c.chunk_index + 1}</span>
                        <div className="flex items-center space-x-2">
                          <span>{c.tokens} Tokens</span>
                          {c.formula_count > 0 && (
                            <span className="bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">公式 {c.formula_count}</span>
                          )}
                          {c.table_count > 0 && (
                            <span className="bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium">表格 {c.table_count}</span>
                          )}
                        </div>
                      </div>
                      <div className="text-[11px] text-slate-700 whitespace-pre-wrap leading-relaxed line-clamp-4">{c.content}</div>
                      {c.section_path && (
                        <div className="text-[10px] text-slate-400 truncate">路径: {c.section_path}</div>
                      )}
                    </div>
                  ))}
                </div>
                <p className="text-[10px] text-slate-400">
                  仅展示前 {previewResult.preview_chunks.length} 个切片；预览为 Dry-run 模式，不会写入知识库。
                </p>
              </div>
            )}
          </div>
        )}

        {/* Upload Status Card */}
        {isUploading && (
          <div className="bg-white border border-blue-200 rounded-[24px] p-4.5 shadow-sm space-y-2">
            <div className="flex items-center space-x-2.5 text-blue-700 font-medium text-xs">
              <RefreshCw className="w-4 h-4 animate-spin text-blue-600" />
              <span>{uploadStep}</span>
            </div>
            <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
              <div className="bg-blue-600 h-full rounded-full animate-pulse w-3/4"></div>
            </div>
          </div>
        )}

        {uploadResult && (
          <div className="bg-emerald-50 border border-emerald-200 rounded-[24px] p-4.5 text-xs space-y-1 text-emerald-800 shadow-xs">
            <div className="font-semibold flex items-center space-x-1.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
              <span>{uploadResult.message}</span>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-emerald-700 pt-1">
              <span>切片数量: {uploadResult.chunks_created} 个</span>
              <span>识别公式: {uploadResult.formula_count} 处</span>
              <span>结构化表格: {uploadResult.table_count} 个</span>
              <span className="bg-white/70 border border-emerald-200 px-2 py-0.5 rounded-full font-medium">
                策略: {uploadResult.chunk_strategy}（{uploadResult.chunk_size} token / {uploadResult.chunk_overlap} overlap）
              </span>
            </div>
          </div>
        )}

        {/* Document List Table */}
        <div className="bg-white border border-slate-200/80 rounded-[28px] shadow-[0_4px_20px_rgba(0,0,0,0.03)] overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
            <h3 className="font-semibold text-slate-800 text-sm">
              已入库文档列表 ({documents.length})
            </h3>
            <span className="text-[11px] text-slate-400">所有切片已同步建立倒排与向量索引</span>
          </div>

          <div className="divide-y divide-slate-100 text-xs">
            {documents.length === 0 ? (
              <div className="p-8 text-center text-slate-400">
                当前知识库暂无文档，请在上方上传课件、教材或论文
              </div>
            ) : (
              documents.map((doc) => (
                <div key={doc.id} className="px-6 py-3.5 flex items-center justify-between hover:bg-slate-50/80 transition-colors">
                  <div className="flex items-center space-x-3 truncate">
                    <div className="w-9 h-9 rounded-2xl bg-slate-100 flex items-center justify-center shrink-0 shadow-xs">
                      {doc.doc_type === "PDF" ? (
                        <FileText className="w-4 h-4 text-red-500" />
                      ) : (
                        <FileSpreadsheet className="w-4 h-4 text-blue-500" />
                      )}
                    </div>
                    <div className="truncate">
                      <div className="font-medium text-slate-800 truncate max-w-md">{doc.filename}</div>
                      <div className="text-[11px] text-slate-400 flex items-center space-x-3 mt-0.5">
                        <span>{(doc.file_size / 1024).toFixed(1)} KB</span>
                        <span>{doc.chunk_count} 个语义切片</span>
                        <span>{doc.formula_count} 个公式</span>
                        <span>{doc.table_count} 个表格</span>
                        <span>{doc.created_at}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center space-x-2 shrink-0">
                    <button
                      onClick={() => handleInspectChunks(doc.id, doc.filename)}
                      className="px-3.5 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-full text-xs flex items-center space-x-1.5 transition-colors font-medium"
                    >
                      <Eye className="w-3.5 h-3.5" />
                      <span>查看切片</span>
                    </button>
                    <button
                      onClick={() => handleDeleteDocument(doc.id)}
                      className="p-2 hover:bg-red-50 text-slate-400 hover:text-red-600 rounded-full transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Search Playground (RAG Testing) */}
        <div className="bg-white border border-slate-200/80 rounded-[28px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.03)] space-y-4">
          <div className="flex items-center space-x-2">
            <Search className="w-4 h-4 text-blue-600" />
            <h3 className="font-semibold text-slate-800 text-sm">检索调试工作台 (Hybrid Search & Rerank)</h3>
          </div>
          <p className="text-xs text-slate-400">
            在当前知识库中测试语义与关键词召回，验证 BM25 稀疏打分、Milvus 稠密打分与 BGE 重排结果。
          </p>

          <div className="flex space-x-2.5">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="输入测试问题，如：导数的几何意义是什么？切线方程如何求解？"
              className="flex-1 bg-slate-50/80 border border-slate-200/80 rounded-full px-5 py-2.5 text-xs text-slate-800 outline-none focus:border-blue-500 focus:bg-white transition-all shadow-inner"
            />
            <button
              onClick={handleSearchPlayground}
              disabled={isSearching || !searchQuery.trim()}
              className="px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-full text-xs font-medium disabled:opacity-50 transition-all flex items-center space-x-1.5 shadow-md shadow-blue-500/20 active:scale-95"
            >
              {isSearching ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
              <span>执行混合检索</span>
            </button>
          </div>

          {searchResults.length > 0 && (
            <div className="space-y-3 pt-2">
              <div className="text-xs font-semibold text-slate-500">
                Top-{searchResults.length} 召回与重排结果：
              </div>
              {searchResults.map((item, idx) => (
                <div key={idx} className="p-4 bg-slate-50/80 border border-slate-200/70 rounded-[22px] text-xs space-y-2.5 shadow-xs">
                  <div className="flex items-center justify-between text-[11px] text-slate-500">
                    <span className="font-medium text-slate-700">
                      #{idx + 1} · {item.doc_name} (第 {item.page_number} 页)
                    </span>
                    <div className="flex items-center space-x-2">
                      <span className="bg-blue-100/70 text-blue-800 px-2.5 py-0.5 rounded-full font-medium">
                        重排得分: {(item.final_score).toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <div className="text-slate-800 whitespace-pre-wrap font-sans bg-white p-3.5 rounded-2xl border border-slate-100/80 leading-relaxed">
                    {item.content}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>

      {/* 3. Chunk Preview Slide-out Drawer */}
      {isPreviewOpen && (
        <div className="w-[480px] bg-white border-l border-slate-200 shadow-2xl flex flex-col h-full z-30 animate-in slide-in-from-right duration-200 md:rounded-l-[32px] overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/60">
            <div>
              <h3 className="font-semibold text-slate-800 text-sm truncate max-w-[340px]">
                {previewDocName} · 切片预览
              </h3>
              <p className="text-[11px] text-slate-400">共 {previewChunks.length} 个语义切片 (512 token + 64 overlap)</p>
            </div>
            <button
              onClick={() => setIsPreviewOpen(false)}
              className="p-2 hover:bg-slate-200/60 rounded-full text-slate-400 hover:text-slate-700 transition-colors"
            >
              ✕
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
            {previewChunks.map((chunk, idx) => (
              <div key={idx} className="p-4 bg-slate-50/80 border border-slate-200/80 rounded-[22px] space-y-2 shadow-xs">
                <div className="flex items-center justify-between text-[10px] text-slate-400">
                  <span className="font-semibold text-blue-600">切片 #{idx + 1}</span>
                  <div className="flex items-center space-x-2">
                    <span>{chunk.tokens} Tokens</span>
                    {chunk.formula_count > 0 && (
                      <span className="bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">
                        LaTeX公式: {chunk.formula_count}
                      </span>
                    )}
                    {chunk.table_count > 0 && (
                      <span className="bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium">
                        表格: {chunk.table_count}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-slate-800 whitespace-pre-wrap font-sans bg-white p-3 rounded-2xl border border-slate-100 leading-relaxed">
                  {chunk.content}
                </div>
                {chunk.metadata?.section_path && (
                  <div className="text-[10px] text-slate-400 truncate">
                    路径: {chunk.metadata.section_path}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
