import type { Metadata } from "next";
import { Roboto, Noto_Sans_SC } from "next/font/google";
import "./globals.css";

// Gemini 风格字体：Roboto 负责拉丁字符（Google Sans 私有字体，Roboto 为公开最接近款），
// Noto Sans SC 负责中文现代无衬线字形
const roboto = Roboto({
  weight: ["300", "400", "500", "700"],
  subsets: ["latin"],
  variable: "--font-roboto",
  display: "swap"
});

const notoSansSC = Noto_Sans_SC({
  weight: ["300", "400", "500", "700"],
  subsets: ["latin"],
  variable: "--font-noto-sans-sc",
  display: "swap",
  preload: false
});

export const metadata: Metadata = {
  title: "智学中台 (EduAgent-Platform) · 数字化教育垂类AI中台",
  description: "基于阿里百炼大模型、Milvus 2.4、Agent Harness 护栏与 LangGraph 多智能体编排的产业级教育中台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={`${roboto.variable} ${notoSansSC.variable}`}>
      <body className="antialiased bg-[#F7F9FC] text-slate-900 overflow-hidden">
        {children}
      </body>
    </html>
  );
}
