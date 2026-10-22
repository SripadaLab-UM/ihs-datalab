import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useSearchParams } from "react-router";

import { api } from "@/api/client";
import { GitHubSignIn } from "@/app/SignIn";
import { HelpPage } from "@/features/help/HelpPage";
import { BASE } from "@/lib/base";
import { KnowledgePage } from "@/features/knowledge/KnowledgePage";
import { PipelinesPage } from "@/features/pipelines/PipelinesPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { SqlPage } from "@/features/sql/SqlPage";
import { WorkflowsPage } from "@/features/workflows/WorkflowsPage";
import { SessionEnded } from "@/features/toolbar/SessionMenu";
import { WorkspacePage } from "@/features/workspace/WorkspacePage";

import { Shell } from "./Shell";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: true, retry: 1 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={BASE || undefined}>
        <Routes>
          {/* Outside the shell: after End session, nothing here may call the API. */}
          <Route path="session-ended" element={<SessionEnded />} />
          <Route element={<Shell />}>
            <Route index element={<Navigate to="/workspace" replace />} />
            <Route path="workspace" element={<WorkspacePage />} />
            <Route path="workspace/:conversationId" element={<WorkspacePage />} />
            {/* Each tab's page is its own file: its work replaces only that page. */}
            <Route path="sql/*" element={<SqlPage />} />
            <Route path="workflows/*" element={<WorkflowsPage />} />
            <Route path="pipelines/*" element={<PipelinesPage />} />
            <Route path="knowledge/*" element={<KnowledgePage />} />
            <Route path="settings/:section?" element={<SettingsPage />} />
            <Route path="help/:slug?" element={<HelpPage />} />
            <Route path="signed-out" element={<SignedOut />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function SignedOut() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [params] = useSearchParams();
  if (health.data?.deployment === "server") return <GitHubSignIn reason={params.get("reason")} />;
  return (
    <div className="flex h-full items-center justify-center text-muted">
      This sign-in link has already been used. Open DataLab from its launcher.
    </div>
  );
}
