"use client";

import React, { useState } from "react";
import { Lock, User, ArrowRight, ShieldCheck, AlertCircle, RefreshCw, CheckCircle2, Mail, GraduationCap } from "lucide-react";
import { loginUser, registerUser } from "@/lib/api";

export default function LoginPage() {
  const [isRegister, setIsRegister] = useState(false);
  const [username, setUsername] = useState("teacher_demo");
  const [password, setPassword] = useState("password123");
  const [email, setEmail] = useState("teacher@edu.ai");
  const [fullName, setFullName] = useState("贾老师 (高级教师)");
  const [role, setRole] = useState("teacher");

  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg("");
    setSuccessMsg("");
    setLoading(true);

    try {
      if (isRegister) {
        await registerUser({
          username: username.trim(),
          email: email.trim(),
          password,
          full_name: fullName.trim() || username.trim(),
          role
        });
        setSuccessMsg("注册成功！正在为您初始化专属教学画像与工作台...");
      } else {
        await loginUser(username.trim(), password);
        setSuccessMsg("登录验证成功！正在进入智能体工作台...");
      }

      setTimeout(() => {
        window.location.href = "/";
      }, 600);
    } catch (err: any) {
      setErrorMsg(err.message || "操作失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  const fillDemoAccount = (u: string, p: string, name: string) => {
    setIsRegister(false);
    setUsername(u);
    setPassword(p);
    setFullName(name);
    setErrorMsg("");
  };

  return (
    <div className="relative min-h-screen w-full overflow-hidden select-none">
      {/* ===== 全屏品牌背景图 ===== */}
      <div
        className="absolute inset-0 bg-cover bg-center bg-no-repeat"
        style={{ backgroundImage: "url('/login.png')" }}
      />

      {/* ===== 遮罩层：桌面端右侧渐变保证卡片可读，移动端整体轻遮罩 ===== */}
      <div className="absolute inset-0 bg-gradient-to-r from-white/10 via-white/30 to-white/75" />
      <div className="absolute inset-0 bg-white/25 md:bg-transparent" />

      {/* ===== 内容区 ===== */}
      <div className="relative z-10 min-h-screen w-full flex items-center justify-center md:justify-end md:pr-[6%] px-4 py-8">

        {/* 小屏长表单可滚动 */}
        <div className="w-full max-h-screen overflow-y-auto flex justify-center">
        {/* 登录卡片：白色玻璃拟态 */}
        <div
          className="w-full max-w-[420px] rounded-[28px] border border-white/70 bg-white/65 p-7 sm:p-8 shadow-[0_24px_70px_-18px_rgba(30,64,175,0.35)] backdrop-blur-2xl animate-[fadeInUp_0.7s_ease-out]"
        >
          {/* 标题区 */}
          <div className="space-y-1.5">
            <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-blue-50/90 border border-blue-100 text-[11px] font-medium text-blue-600">
              <GraduationCap className="w-3.5 h-3.5" />
              教育智能工作台
            </div>
            <h2 className="text-[22px] font-bold text-slate-800 tracking-tight pt-1">
              {isRegister ? "创建教学账户" : "欢迎回来 👋"}
            </h2>
            <p className="text-xs text-slate-500">
              {isRegister
                ? "注册即自动生成专属教学画像与记忆空间"
                : "登录后继续您的智能备课与教研工作"}
            </p>
          </div>

          {/* Tab Switcher */}
          <div className="flex bg-slate-100/80 p-1 rounded-full text-xs font-medium text-slate-500 mt-5">
            <button
              type="button"
              onClick={() => { setIsRegister(false); setErrorMsg(""); setSuccessMsg(""); }}
              className={`flex-1 py-2 rounded-full transition-all ${
                !isRegister ? "bg-white text-blue-700 shadow-sm font-semibold" : "hover:text-slate-700"
              }`}
            >
              登录
            </button>
            <button
              type="button"
              onClick={() => { setIsRegister(true); setErrorMsg(""); setSuccessMsg(""); }}
              className={`flex-1 py-2 rounded-full transition-all ${
                isRegister ? "bg-white text-blue-700 shadow-sm font-semibold" : "hover:text-slate-700"
              }`}
            >
              注册
            </button>
          </div>

          {/* 快速测试 */}
          {!isRegister && (
            <div className="flex items-center gap-2 text-[11px] mt-4">
              <span className="text-slate-400 shrink-0">快速体验：</span>
              <button
                type="button"
                onClick={() => fillDemoAccount("teacher_demo", "password123", "贾老师 (高级教师)")}
                className="px-2.5 py-1 rounded-full bg-blue-50/90 text-blue-600 border border-blue-200/70 hover:bg-blue-100 transition-colors font-medium"
              >
                贾老师
              </button>
              <button
                type="button"
                onClick={() => fillDemoAccount("admin", "admin123", "系统管理员")}
                className="px-2.5 py-1 rounded-full bg-slate-100/90 text-slate-600 border border-slate-200 hover:bg-slate-200 transition-colors"
              >
                管理员
              </button>
            </div>
          )}

          {/* Alerts */}
          {errorMsg && (
            <div className="p-3 mt-4 bg-red-50 border border-red-200 text-red-700 rounded-2xl text-xs flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 text-red-500" />
              <span>{errorMsg}</span>
            </div>
          )}
          {successMsg && (
            <div className="p-3 mt-4 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-2xl text-xs flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-500" />
              <span>{successMsg}</span>
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-3.5 text-xs mt-4">
            <div className="space-y-1.5">
              <label className="text-slate-600 font-medium px-1">用户名</label>
              <div className="relative flex items-center">
                <User className="w-4 h-4 text-slate-400 absolute left-3.5" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  className="w-full pl-10 pr-4 py-2.5 bg-white/70 border border-slate-200 rounded-xl outline-none focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100/60 text-slate-800 transition-all"
                  placeholder="请输入用户名"
                />
              </div>
            </div>

            {isRegister && (
              <>
                <div className="space-y-1.5">
                  <label className="text-slate-600 font-medium px-1">真实姓名 / 称谓</label>
                  <input
                    type="text"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    required
                    className="w-full px-4 py-2.5 bg-white/70 border border-slate-200 rounded-xl outline-none focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100/60 text-slate-800 transition-all"
                    placeholder="如：李老师（高中物理备课组长）"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-slate-600 font-medium px-1">工作邮箱</label>
                  <div className="relative flex items-center">
                    <Mail className="w-4 h-4 text-slate-400 absolute left-3.5" />
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                      className="w-full pl-10 pr-4 py-2.5 bg-white/70 border border-slate-200 rounded-xl outline-none focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100/60 text-slate-800 transition-all"
                      placeholder="teacher@school.edu.cn"
                    />
                  </div>
                </div>
              </>
            )}

            <div className="space-y-1.5">
              <label className="text-slate-600 font-medium px-1">密码</label>
              <div className="relative flex items-center">
                <Lock className="w-4 h-4 text-slate-400 absolute left-3.5" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full pl-10 pr-4 py-2.5 bg-white/70 border border-slate-200 rounded-xl outline-none focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100/60 text-slate-800 transition-all"
                  placeholder="••••••••"
                />
              </div>
            </div>

            {isRegister && (
              <div className="space-y-1.5">
                <label className="text-slate-600 font-medium px-1">用户角色</label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="w-full px-4 py-2.5 bg-white/70 border border-slate-200 rounded-xl outline-none focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100/60 text-slate-800 transition-all"
                >
                  <option value="teacher">学科任课教师</option>
                  <option value="researcher">教研员 / 课题负责人</option>
                  <option value="admin">教学督导 / 管理员</option>
                </select>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white font-medium rounded-xl shadow-lg shadow-blue-500/25 flex items-center justify-center gap-1.5 transition-all active:scale-[0.98] mt-2 disabled:opacity-60"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>正在验证安全凭据...</span>
                </>
              ) : (
                <>
                  <span>{isRegister ? "创建账户" : "登 录"}</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          {/* 安全标识 */}
          <div className="flex items-center justify-center pt-4 mt-4 border-t border-slate-200/70">
            <div className="inline-flex items-center gap-1.5 text-[11px] text-slate-400">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
              <span>Agent Harness 安全护栏已生效</span>
            </div>
          </div>
        </div>
        </div>
      </div>
    </div>
  );
}
