/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        darkBg: "#0B0F19",
        cardBg: "#111827",
        cardBorder: "#1E293B",
        accentTeal: "#0D9488",
        accentBlue: "#38BDF8",
        accentPurple: "#6366F1",
        accentGreen: "#10B981",
        accentAmber: "#F59E0B",
        accentRed: "#EF4444"
      }
    },
  },
  plugins: [],
}

