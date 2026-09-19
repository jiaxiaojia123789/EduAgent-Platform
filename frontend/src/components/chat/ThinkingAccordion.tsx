"use client";

import React, { useState } from "react";
import { ChevronDown, ChevronUp, Sparkles, CheckCircle2, ShieldCheck, Database, Wrench } from "lucide-react";
import { TraceStep } from "@/lib/api";

interface ThinkingAccordionProps {
  steps?: TraceStep[];
  totalLatencyMs?: number;
}

export const ThinkingAccordion: React.FC<ThinkingAccordionProps> = ({ steps = [], totalLatencyMs = 0 }) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!steps || steps.length === 0) {
    return null;
  }

  const seconds = (totalLatencyMs / 1000).toFixed(1);

  const getStepIcon = (actionType: string) => {
    switch (actionType) {
      case "THINKING":
        return <Sparkles className="w-3.5 h-3.5 text-blue-500" />;
      case "TOOL_CALL":
        return <Database className="w-3.5 h-3.5 text-emerald-500" />;
      case "HITL_GATE":
        return <ShieldCheck className="w-3.5 h-3.5 text-amber-500" />;
      default:
        return <CheckCircle2 className="w-3.5 h-3.5 text-slate-400" />;
    }
  };

  return (
    <div className="mb-3 border border-slate-200/70 bg-slate-50/60 rounded-[20px] overflow-hidden transition-all text-xs shadow-xs">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-slate-100/70 transition-colors text-slate-600 font-medium select-none"
      >
        <div className="flex items-center space-x-2">
          <Sparkles className="w-3.5 h-3.5 text-blue-600 animate-pulse" />
          <span>已完成深度思考与执行流程</span>
          <span className="text-slate-400 font-normal">
            ({steps.length} 步 · 约 {seconds}s)
          </span>
        </div>
        {isOpen ? (
          <ChevronUp className="w-3.5 h-3.5 text-slate-400" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
        )}
      </button>

      {isOpen && (
        <div className="p-3.5 border-t border-slate-200/50 bg-white/80 space-y-2.5 rounded-b-[20px]">

          {steps.map((step, idx) => (
            <div key={idx} className="flex items-start space-x-2.5 text-slate-700">
              <div className="mt-0.5 shrink-0">{getStepIcon(step.action_type)}</div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-slate-800">{step.title}</span>
                  {step.elapsed_ms !== undefined && (
                    <span className="text-[10px] text-slate-400">{step.elapsed_ms}ms</span>
                  )}
                </div>
                {step.detail && (
                  <p className="mt-0.5 text-slate-500 text-[11px] leading-relaxed break-words">
                    {step.detail}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
