/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        tg: {
          DEFAULT: "#F68B1F",
          50: "#FEF3E7",
          300: "#F9B266",
          400: "#F79C3F",
          500: "#F68B1F",
          600: "#D9730C",
          700: "#A9590A",
        },
        ink: {
          950: "#07090D",
          900: "#0C1016",
          850: "#10151D",
          800: "#151B24",
          700: "#1E2631",
          600: "#2A3441",
          500: "#3A4655",
        },
      },
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "system-ui", "Segoe UI", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
        serif: ["Georgia", "Cambria", '"Times New Roman"', "serif"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
      },
      keyframes: {
        "pulse-ring": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(246,139,31,0.55)" },
          "50%": { boxShadow: "0 0 0 6px rgba(246,139,31,0)" },
        },
      },
      animation: { "pulse-ring": "pulse-ring 2.2s ease-in-out infinite" },
    },
  },
  plugins: [],
};
