import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#08070d",
          900: "#0d0b14",
          850: "#12101c",
          800: "#181525",
          700: "#221e33",
          600: "#2e2947",
        },
        violet: {
          glow: "#a78bfa",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 24px rgba(139, 92, 246, 0.25)",
        card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 8px 24px rgba(0,0,0,0.4)",
      },
      backgroundImage: {
        "purple-radial":
          "radial-gradient(ellipse 80% 50% at 50% -20%, rgba(124, 58, 237, 0.25), transparent)",
      },
    },
  },
  plugins: [],
};
export default config;
