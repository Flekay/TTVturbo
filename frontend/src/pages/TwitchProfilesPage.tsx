import { TwitchProfilesPanel } from "../features/vodPipeline";
import { TwitchStatusBanner } from "../features/vodPipeline";

export function TwitchProfilesPage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Twitch-Profile</h1>
          <p className="page__description">
            Twitch-Channel-Profile anlegen, aktualisieren und entfernen.
          </p>
        </div>
      </div>

      <section className="page__section">
        <TwitchStatusBanner />
      </section>

      <section className="page__section">
        <TwitchProfilesPanel />
      </section>
    </div>
  );
}
