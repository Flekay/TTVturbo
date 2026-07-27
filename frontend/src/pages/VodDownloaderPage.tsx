import { VodPipelinePanel } from "../features/vodPipeline";

/**
 * VOD Downloader page (renamed from the old "VOD Pipeline").
 *
 * The existing download functionality is preserved unchanged — the same
 * VodPipelinePanel component is reused. Only the page title and route
 * changed: /vod-pipeline -> /vod-downloader.
 */
export function VodDownloaderPage() {
  return (
    <div className="page">
      <section className="page__section">
        <VodPipelinePanel />
      </section>
    </div>
  );
}
