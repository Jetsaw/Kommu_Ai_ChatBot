import { useEffect, useState } from "react";

function TestAPI() {
  const [status, setStatus] = useState("Checking API...");

  useEffect(() => {
  
    const BASE_URL = import.meta.env.VITE_API_BASE || "/api";


    fetch(`${BASE_URL}/health`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setStatus(` API Connected: ${data.status}`);
      })
      .catch((err) => {
        setStatus(` API Connection Failed: ${err.message}`);
      });
  }, []);

  return (
    <div className="p-6 text-center">
      <h2 className="text-2xl font-bold mb-3 text-gray-800">API Connection Test</h2>
      <p className="text-lg">{status}</p>
    </div>
  );
}

export default TestAPI;
