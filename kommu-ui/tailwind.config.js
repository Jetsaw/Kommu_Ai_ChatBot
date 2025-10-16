/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        kommu: {
          blue: "#004aad",
          gray: "#f2f4f8",
          dark: "#1a1a1a"
        }
      }
    }
  },
  plugins: []
}
