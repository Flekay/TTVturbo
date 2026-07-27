import { useNavigate } from "react-router-dom";
import { ProfileSelector } from "./ProfileSelector";
import { ImportVodPanel } from "./ImportVodPanel";
import { VodList } from "./VodList";
import { useActiveProfileStore } from "./activeProfileStore";

/**
 * Top-level VOD Pipeline panel.
 *
 * Two distinct workflows, visually separated by a divider:
 *  1. VOD-Link import (top, full width) — paste a twitch.tv URL.
 *  2. Profile-based browse/sync/download (below) — profile selector +
 *     VOD list with download controls.
 * All server state is TanStack Query; only the active profile id is
 * persisted across reloads (via zustand/persist in activeProfileStore).
 */
export function VodPipelinePanel() {
  const navigate = useNavigate();
  const activeProfileId = useActiveProfileStore((s) => s.activeProfileId);

  return (
    <div className="vp-vod-pipeline">
      <ImportVodPanel profileId={activeProfileId} />
      <hr className="vp-vod-pipeline__divider" />
      <div className="vp-vod-pipeline__layout">
        <ProfileSelector onOpenProfilesPage={() => navigate("/twitch-profiles")} />
        <div className="vp-vod-pipeline__main">
          <VodList profileId={activeProfileId} />
        </div>
      </div>
    </div>
  );
}
