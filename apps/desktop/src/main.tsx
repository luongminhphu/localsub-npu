import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
// Bundled fonts (offline, CSP 'self'): Latin + Vietnamese only; CJK/Hangul use Windows fonts.
import "@fontsource/fira-sans/latin-400.css";
import "@fontsource/fira-sans/latin-ext-400.css";
import "@fontsource/fira-sans/vietnamese-400.css";
import "@fontsource/fira-sans/latin-500.css";
import "@fontsource/fira-sans/latin-ext-500.css";
import "@fontsource/fira-sans/vietnamese-500.css";
import "@fontsource/fira-sans/latin-600.css";
import "@fontsource/fira-sans/latin-ext-600.css";
import "@fontsource/fira-sans/vietnamese-600.css";
import "@fontsource/fira-sans/latin-700.css";
import "@fontsource/fira-sans/latin-ext-700.css";
import "@fontsource/fira-sans/vietnamese-700.css";
import "@fontsource/fira-code/latin-400.css";
import "@fontsource/fira-code/latin-ext-400.css";
import "@fontsource/fira-code/latin-500.css";
import "@fontsource/fira-code/latin-ext-500.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
