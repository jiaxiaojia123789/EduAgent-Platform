"use client";

import React, { useState } from "react";
import { BookOpen, ExternalLink } from "lucide-react";
import { CitationItem } from "@/lib/api";

interface CitationPopoverProps {
  citation: CitationItem;
}

export const CitationPopover: React.FC<CitationPopoverProps> = ({ citation }) => {
  const [showTooltip, setShowTooltip] = useState(false);

  return (
    <span className="relative inline-block mx-0.5">
      <button
        type="button"
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
        className="inline-flex items-center px-2 py-0.5 text-[11px] font-semibold text-blue-600 bg-blue-50/90 border border-blue-200/80 rounded-full hover:bg-blue-100 hover:text-blue-800 transition-colors shadow-xs"
      >
        [{citation.citation_id}]
      </button>

      {showTooltip && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 p-3.5 bg-slate-900/95 backdrop-blur-md text-white rounded-2xl shadow-[0_10px_30px_rgba(0,0,0,0.25)] text-xs space-y-1.5 pointer-events-none animate-in fade-in zoom-in-95 duration-150 border border-slate-700/50">

          <div className="flex items-center justify-between border-b border-slate-700/60 pb-1 text-slate-300 font-medium">
            <span className="flex items-center space-x-1 truncate max-w-[180px]">
              <BookOpen className="w-3.5 h-3.5 text-blue-400 shrink-0 inline mr-1" />
              {citation.title}
            </span>
            <span className="text-[10px] text-emerald-400 bg-emerald-950/80 px-1.5 py-0.5 rounded">
              P{citation.page_number} · 置信度 {(citation.confidence_score * 100).toFixed(0)}%
            </span>
          </div>
          <p className="text-slate-300 text-[11px] leading-relaxed line-clamp-3">
            {citation.snippet}
          </p>
          <div className="text-[10px] text-slate-500 pt-0.5">
            来源: Milvus 2.4 混合检索 + BGE-Reranker
          </div>
        </div>
      )}
    </span>
  );
};
