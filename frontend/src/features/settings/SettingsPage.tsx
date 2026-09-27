import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

import { AboutSection } from "./sections/AboutSection";
import { ConnectionsSection } from "./sections/ConnectionsSection";
import { DiagnosticsSection } from "./sections/DiagnosticsSection";
import { ExportDestinations } from "./sections/ExportDestinations";
import { GitHubSection } from "./sections/GitHubSection";
import { SafetySection } from "./sections/SafetySection";
import { StorageSection } from "./sections/StorageSection";
import { UpdatesSection } from "./sections/UpdatesSection";

// Each section is its own file in ./sections. The ones not built yet render
// nothing, so their work changes only its own file.
export function SettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-14 px-8 py-12">
        <SafetySection />
        <ConnectionsSection />
        <GitHubSection />
        <ExportDestinations practice={health.data?.profile === "practice"} />
        <StorageSection />
        <UpdatesSection />
        <DiagnosticsSection />
        <AboutSection health={health.data} />
      </div>
    </div>
  );
}
