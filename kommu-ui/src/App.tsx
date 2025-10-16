import TestAPI from "./components/TestAPI";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50">
      <h1 className="text-3xl font-bold mb-6 text-blue-700">Kommu Dashboard</h1>
      <TestAPI />
      <footer className="mt-10 text-sm text-gray-500">
        © 2025 Kommu Sdn. Bhd. – Internal Dashboard
      </footer>
    </div>
  );
}
