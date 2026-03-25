/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: {} },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      {
        voyagers: {
          "primary": "#4F46E5",
          "primary-content": "#ffffff",
          "secondary": "#0EA5E9",
          "secondary-content": "#ffffff",
          "accent": "#F59E0B",
          "accent-content": "#000000",
          "neutral": "#374151",
          "neutral-content": "#ffffff",
          "base-100": "#ffffff",
          "base-200": "#F9FAFB",
          "base-300": "#F3F4F6",
          "base-content": "#111827",
          "info": "#3B82F6",
          "info-content": "#ffffff",
          "success": "#10B981",
          "success-content": "#ffffff",
          "warning": "#F59E0B",
          "warning-content": "#000000",
          "error": "#EF4444",
          "error-content": "#ffffff",
        },
      },
    ],
  },
};
