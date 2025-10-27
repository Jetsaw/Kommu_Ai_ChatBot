/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'kommu-blue': '#007bff',
        'kommu-dark': '#0056b3',
        'kommu-gray': '#f8fafc',
        'kommu-text': '#1f2937',
      },
    },
  },
  plugins: [],
};
