import { VodPipelinePanel } from "../features/vodPipeline";

export function VodPipelinePage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">VOD Pipeline</h1>
          <p className="page__description">
            Twitch-Profile verwalten, VODs synchronisieren und herunterladen.
          </p>
        </div>
      </div>

      <section className="page__section">
        <VodPipelinePanel />
      </section>
    </div>
  );
}
