import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {StrictMode} from "react";
import {createRoot} from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./styles.css";
import {TopologyApp} from "./TopologyApp";

const queryClient = new QueryClient({defaultOptions: {queries: {staleTime: 5_000, retry: 1}}});

createRoot(document.getElementById("root")!).render(
  <StrictMode><QueryClientProvider client={queryClient}><TopologyApp /></QueryClientProvider></StrictMode>,
);
