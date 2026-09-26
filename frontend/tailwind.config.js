/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        ink: "var(--text)",
        muted: "var(--text-muted)",
        accent: "var(--brand-indigo)",
        header: "var(--brand-header)",
        nav: "var(--nav-bg)",
        danger: "var(--danger)",
        action: "var(--action)",
      },
    },
  },
  plugins: [],
};
