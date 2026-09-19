"use client";

import React, { useState } from "react";
import { X, Download, Copy, Check, FileText, Sparkles, BookOpen } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { exportWordDocument, exportPdfDocument } from "@/lib/api";

/** 预处理 LLM 输出的 markdown：去除行尾空格 + 标题行前导空格 + 统一换行 + 规范化列表标记 */
function preprocessMarkdown(raw: string): string {
  if (!raw) return "";
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const result: string[] = [];
  for (const line of lines) {
    let l = line.replace(/\s+$/, "");
    if (/^\s+#{1,6}\s/.test(l)) {
      l = l.replace(/^\s+/, "");
    }
    if (/^\s*\*\s+/.test(l)) {
      l = l.replace(/^(\s*)\*\s+/, "$1- ");
    }
    result.push(l);
  }
  return result.join("\n");
}

interface ArtifactCanvasProps {
  isOpen: boolean;
  onClose: () => void;
  artifact: any;
}

export const ArtifactCanvas: React.FC<ArtifactCanvasProps> = ({
  isOpen,
  onClose,
  artifact
}) => {
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadingPdf, setDownloadingPdf] = useState(false);

  if (!isOpen || !artifact) {
    return null;
  }

  const title = artifact.title || "教学成果协同工件";
  const markdownContent = artifact.markdown || "";

  const handleCopy = () => {
    navigator.clipboard.writeText(markdownContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  /** 通用文件下载：创建临时链接触发浏览器保存 */
  const downloadBlob = (blob: Blob, filename: string) => {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  };

  const handleExportWord = async () => {
    try {
      setDownloading(true);
      const blob = await exportWordDocument(title, markdownContent);
      downloadBlob(blob, `${title}.docx`);
    } catch (err) {
      console.error("Export word failed:", err);
      alert("导出失败，请检查网络连接");
    } finally {
      setDownloading(false);
    }
  };

  const handleExportPdf = async () => {
    try {
      setDownloadingPdf(true);
      const blob = await exportPdfDocument(title, markdownContent);
      downloadBlob(blob, `${title}.pdf`);
    } catch (err) {
      console.error("Export pdf failed:", err);
      alert("导出失败，请检查网络连接");
    } finally {
      setDownloadingPdf(false);
    }
  };

  return (
    <div className="w-[480px] lg:w-[540px] bg-white border-l border-slate-200/80 shadow-2xl flex flex-col h-full z-20 animate-in slide-in-from-right duration-200 md:rounded-l-[32px] overflow-hidden">
      {/* Canvas Header */}
      <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/60">
        <div className="flex items-center space-x-2.5">
          <div className="w-9 h-9 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shadow-xs">
            <FileText className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="font-semibold text-slate-800 text-sm truncate max-w-[220px]">
                {title}
              </h3>
              <span className="text-[10px] bg-emerald-50 text-emerald-600 font-medium px-2.5 py-0.5 rounded-full border border-emerald-200 shadow-xs">
                可交付工件
              </span>
            </div>
            <p className="text-[11px] text-slate-400">已对标新课标核心素养与格式约束</p>
          </div>
        </div>

        <div className="flex items-center space-x-1.5">
          <button
            onClick={handleCopy}
            title="复制 Markdown"
            className="p-2 hover:bg-slate-200/60 rounded-full text-slate-500 transition-colors text-xs flex items-center justify-center"
          >
            {copied ? <Check className="w-4 h-4 text-emerald-600" /> : <Copy className="w-4 h-4" />}
          </button>
          <button
            onClick={handleExportWord}
            disabled={downloading}
            className="flex items-center space-x-1.5 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-full text-xs font-medium shadow-md shadow-blue-500/20 transition-all disabled:opacity-50 active:scale-95"
          >
            <Download className="w-3.5 h-3.5" />
            <span>{downloading ? "生成中..." : "导出 Word"}</span>
          </button>
          <button
            onClick={handleExportPdf}
            disabled={downloadingPdf}
            className="flex items-center space-x-1.5 px-4 py-2 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-700 hover:to-red-700 text-white rounded-full text-xs font-medium shadow-md shadow-rose-500/20 transition-all disabled:opacity-50 active:scale-95"
          >
            <Download className="w-3.5 h-3.5" />
            <span>{downloadingPdf ? "生成中..." : "导出 PDF"}</span>
          </button>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-200/60 rounded-full text-slate-400 hover:text-slate-700 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Artifact Content Preview - Markdown + LaTeX 渲染 */}
      <div className="flex-1 overflow-y-auto p-6 font-sans text-sm text-slate-700 leading-relaxed">
        <div className="bg-slate-50/80 p-5 rounded-[24px] border border-slate-200/70 text-slate-800 text-xs shadow-xs">
          <div className="prose prose-slate prose-sm max-w-none
            prose-headings:font-bold prose-headings:text-slate-800
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
            prose-th:border prose-th:border-slate-300 prose-th:bg-slate-100 prose-th:px-3 prose-th:py-1.5 prose-th:text-left prose-th:font-semibold
            prose-td:border prose-td:border-slate-300 prose-td:px-3 prose-td:py-1.5
            prose-hr:border-slate-200 prose-hr:my-4
            [&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex]:text-base
          ">
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex]}
            >
              {preprocessMarkdown(markdownContent || "_暂无内容_")}
            </ReactMarkdown>
          </div>
        </div>
      </div>

      {/* Footer Info */}
      <div className="p-3.5 border-t border-slate-100 bg-slate-50/60 flex items-center justify-between text-[11px] text-slate-400">
        <span>支持导入希沃白板 / 智慧课堂系统</span>
        <span>版本 v1.0 · 阿里百炼 Qwen 驱动</span>
      </div>
    </div>
  );
};
