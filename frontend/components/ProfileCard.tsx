/* eslint-disable @next/next/no-img-element -- remote platform avatars, displayed as-is */
import { useState } from "react";

import { Badge } from "@/components/ui";
import { platformLabel } from "@/lib/format";
import type { ProfileOut } from "@/services/api";

export default function ProfileCard({
  title,
  profile,
  country,
  missingText = "Account not found",
  socials,
}: {
  title: string;
  profile: ProfileOut | null | undefined;
  country?: string | null;
  missingText?: string;
  socials?: { kind: string; identity: string }[];
}) {
  return (
    <div className="flex-1 rounded-lg border border-zinc-200 bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</div>
      {!profile ? (
        <p className="mt-3 text-sm text-zinc-500">{missingText}</p>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-3">
            <Avatar key={profile.profile_image_url ?? profile.username} url={profile.profile_image_url} name={profile.username} />
            <div>
              <div className="flex items-center gap-2">
                <Badge tone={profile.platform === "kick" ? "success" : "info"}>{platformLabel(profile.platform)}</Badge>
                <span className="font-semibold text-zinc-900">{profile.username}</span>
              </div>
              {profile.display_name && <div className="text-sm text-zinc-600">{profile.display_name}</div>}
              {profile.profile_url && (
                <a
                  href={profile.profile_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-brand-600 hover:underline"
                >
                  {profile.profile_url}
                </a>
              )}
            </div>
          </div>
          <dl className="grid grid-cols-3 gap-x-3 gap-y-1 text-sm">
            {country !== undefined && (
              <>
                <dt className="text-zinc-500">Country</dt>
                <dd className="col-span-2">{country || "—"}</dd>
              </>
            )}
            <dt className="text-zinc-500">Category</dt>
            <dd className="col-span-2">{profile.category || "—"}</dd>
            <dt className="text-zinc-500">Language</dt>
            <dd className="col-span-2">{profile.language || "—"}</dd>
            <dt className="text-zinc-500">Stream title</dt>
            <dd className="col-span-2 truncate">{profile.stream_title || "—"}</dd>
          </dl>
          {profile.description && (
            <p className="whitespace-pre-line rounded-md bg-zinc-50 p-2 text-sm text-zinc-700">{profile.description}</p>
          )}
          {socials && socials.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {socials.map((s) => (
                <Badge key={`${s.kind}:${s.identity}`}>
                  {s.kind}: {s.identity}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Avatar({ url, name }: { url?: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) {
    return (
      <div
        className="grid h-16 w-16 shrink-0 place-items-center rounded-full bg-zinc-100 text-lg font-semibold uppercase text-zinc-400"
        title={url ? "image unavailable" : "no profile image"}
      >
        {name.slice(0, 2)}
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={`${name} avatar`}
      onError={() => setFailed(true)}
      className="h-16 w-16 shrink-0 rounded-full border border-zinc-200 object-cover"
      referrerPolicy="no-referrer"
    />
  );
}
