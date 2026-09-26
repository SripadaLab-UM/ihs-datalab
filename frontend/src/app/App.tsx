import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { SettingsPage } from "@/features/settings/SettingsPage";
import { WorkspacePage } from "@/features/workspace/WorkspacePage";

import { ComingSoon, Shell } from "./Shell";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: true, retry: 1 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Navigate to="/workspace" replace />} />
            <Route path="workspace" element={<WorkspacePage />} />
            <Route path="workspace/:conversationId" element={<WorkspacePage />} />
            <Route path="sql" element={<ComingSoon name="SQL Playground" />} />
            <Route path="workflows" element={<ComingSoon name="Workflows" />} />
            <Route path="pipelines" element={<ComingSoon name="Pipelines" />} />
            <Route path="knowledge" element={<ComingSoon name="Knowledge" />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="signed-out" element={<SignedOut />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function SignedOut() {
  return (
    <div className="flex h-full items-center justify-center text-muted">
      This sign-in link has already been used. Open DataLab from its launcher.
    </div>
  );
}
