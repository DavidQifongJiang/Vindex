import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { initializeTheme } from "./lib/theme";
import "./styles.css";

initializeTheme();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
