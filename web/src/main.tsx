import { ToastProvider } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HostProvider, browserHost } from "./HostCapabilities";
import { App } from "./App";
import "./style.css";
import { createBrowserRouter, RouterProvider } from "react-router";
import { workspacePaths } from "./navigation";
const router = createBrowserRouter([
  {
    element: (
      <HostProvider host={browserHost}>
        <App />
      </HostProvider>
    ),
    children: [
      { path: "/" },
      ...workspacePaths.map((path) => ({ path })),
      { path: "*", id: "not-found" },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider delayDuration={0}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </TooltipProvider>
  </StrictMode>,
);
