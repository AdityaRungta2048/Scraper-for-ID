import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Alert } from "@/components/ui";
import { api, type AppConfig } from "@/services/api";

export default function Layout({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);

  const missing = config ? [!config.twitch_configured && "Twitch", !config.kick_configured && "Kick"].filter(Boolean) : [];

  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">ID</span>
            <span>
              <span className="block text-sm font-bold tracking-wide text-zinc-900">CROSS-PLATFORM STREAMER MATCHER</span>
              <span className="block text-xs text-zinc-500">Kick ⇄ Twitch identity resolution</span>
            </span>
          </Link>
          {config && (
            <span className="hidden text-xs text-zinc-500 sm:block">
              engine v{config.matching_engine_version} · match ≥ {config.match_threshold} · review ≥{" "}
              {config.review_threshold}
            </span>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
        {missing.length > 0 && (
          <Alert tone="warning" title={`${missing.join(" and ")} API credentials are not configured`}>
            Set the client id/secret environment variables on the backend (see README → Twitch/Kick API setup). Jobs
            will fail with a configuration error until they are set.
          </Alert>
        )}
        {children}
      </main>
    </div>
  );
}
