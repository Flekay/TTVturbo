import { VoiceProfilesPanel } from "../features/voiceProfiles";

export function VoiceLabPage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Voice Profiles</h1>
          <p className="page__description">
            Voice-Profile verwalten und akzeptierte Referenzen für Voice-Clones pflegen.
          </p>
        </div>
      </div>

      <section className="page__section">
        <VoiceProfilesPanel />
      </section>
    </div>
  );
}
