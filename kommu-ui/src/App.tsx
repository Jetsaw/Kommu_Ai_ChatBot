import React from "react";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";

function App() {
  const token = localStorage.getItem("agent_token");
  return token ? <Dashboard /> : <Login />;
}

export default App;
