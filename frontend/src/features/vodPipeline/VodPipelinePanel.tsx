import { useNavigate } from "react-router-dom";
import { ProfileSelector } from "./ProfileSelector";
import { ImportVodPanel } from "./ImportVodPanel";
import { VodList } from "./VodList";
import { useActiveProfileStore } from "./activeProfileStore";

/**
 * Top-level VOD Pipeline panel.
 *
 * Composes the profile selector, the manual VOD import form and the VOD
 * list with download controls. All server state is TanStack Query; only
 * the active profile id is persisted across reloads (via zustand/persist
 * in activeProfileStore).
 */
export function VodPipelinePanel() {
  const navigate = useNavigate();
  const activeProfileId = useActiveProfileStore((s) => s.activeProfileId);

  return (
    <div className="vp-vod-pipeline">
      <div className="vp-vod-pipeline__layout">
        <ProfileSelector onOpenProfilesPage={() => navigate("/twitch-profiles")} />
        <div className="vp-vod-pipeline__main">
          <ImportVodPanel profileId={activeProfileId} />
          <VodList profileId={activeProfileId} />
        </div>
      </div>
    </div>
  );
}
