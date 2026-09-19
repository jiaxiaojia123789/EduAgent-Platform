/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        // 全站默认字体：方正小标宋简体优先，未安装时回退宋体（SimSun / macOS Songti SC）
        sans: ['"方正小标宋简体"', '"FZXiaoBiaoSong-B05S"', '"SimSun"', '"宋体"', '"NSimSun"', '"Songti SC"', '"STSong"', 'serif'],
        serif: ['"方正小标宋简体"', '"FZXiaoBiaoSong-B05S"', '"SimSun"', '"宋体"', '"NSimSun"', '"Songti SC"', '"STSong"', 'serif'],
      },
      colors: {
        doubao: {
          blue: "#3b82f6",
          dark: "#0f172a",
          surface: "#f8fafc",
          card: "#ffffff",
          border: "#e2e8f0",
          text: "#1e293b",
          subtext: "#64748b"
        }
      }
    },
  },
  plugins: [require("@tailwindcss/typography")],
}
