import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { Crop, X } from "lucide-react";
import { useRegionSelection, type NormalizedRegion } from "./useRegionSelection";

interface RegionPickerProps {
  /** URL of the video to preview (e.g. libraryFileUrl(itemId)). */
  videoUrl: string;
  /** Currently committed region, or null if nothing selected. */
  region: NormalizedRegion | null;
  /** Called whenever the committed region changes (after a drag finishes). */
  onChange: (region: NormalizedRegion | null) => void;
  /** Optional label shown above the picker. */
  label?: string;
  /** Disable interaction (e.g. while a job is running). */
  disabled?: boolean;
}

/**
 * Visual region selector over a video preview.
 *
 * Renders a `<video>` with an overlay surface. The user draws a rectangle by
 * pointer-down + drag on the surface, then can move or resize it via the
 * four corner handles. The selection is emitted as normalized 0..1
 * coordinates — exactly what the backend `StartCutRequest.region` expects.
 *
 * The component is uncontrolled-ish: it owns the live drag state via
 * `useRegionSelection` and mirrors the committed value back to the parent
 * through `onChange`. The parent's `region` prop is used only as the initial
 * value; subsequent external changes are ignored to avoid feedback loops.
 */
export function RegionPicker({ videoUrl, region: initial, onChange, label, disabled }: RegionPickerProps) {
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const selection = useRegionSelection(surfaceRef, initial);

  // Propagate committed changes upward. We track the last emitted value to
  // avoid redundant calls when the parent re-renders with the same value.
  const lastEmittedRef = useRef<NormalizedRegion | null>(initial ?? null);
  useEffect(() => {
    if (selection.committed !== lastEmittedRef.current) {
      lastEmittedRef.current = selection.committed;
      onChange(selection.committed);
    }
  }, [selection.committed, onChange]);

  const region = selection.region;

  return (
    <div className="region-picker">
      {label && <div className="region-picker__label">{label}</div>}
      <div className="region-picker__stage">
        <video
          className="region-picker__video"
          src={videoUrl}
          preload="metadata"
          muted
          playsInline
          tabIndex={-1}
        />
        <div
          ref={surfaceRef}
          className={`region-picker__surface${disabled ? " is-disabled" : ""}${region ? " has-selection" : ""}`}
          onPointerDown={disabled ? undefined : (event: ReactPointerEvent<HTMLDivElement>) => selection.beginDraw(event)}
        >
          {!region && !disabled && (
            <div className="region-picker__hint">
              <Crop size={20} />
              <span>Ziehe ein Rechteck, um den Bereich zu wählen</span>
            </div>
          )}
          {region && region.width > 0 && region.height > 0 && (
            <div
              className="region-picker__rect"
              style={{
                left: `${region.x * 100}%`,
                top: `${region.y * 100}%`,
                width: `${region.width * 100}%`,
                height: `${region.height * 100}%`,
              }}
              onPointerDown={disabled ? undefined : (event) => { event.stopPropagation(); selection.beginAdjust(event, "move"); }}
            >
              {!disabled && (
                <>
                  <span className="region-picker__handle region-picker__handle--nw" onPointerDown={(event) => selection.beginAdjust(event, "resize-nw")} />
                  <span className="region-picker__handle region-picker__handle--ne" onPointerDown={(event) => selection.beginAdjust(event, "resize-ne")} />
                  <span className="region-picker__handle region-picker__handle--sw" onPointerDown={(event) => selection.beginAdjust(event, "resize-sw")} />
                  <span className="region-picker__handle region-picker__handle--se" onPointerDown={(event) => selection.beginAdjust(event, "resize-se")} />
                  <button
                    type="button"
                    className="region-picker__clear"
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => { event.stopPropagation(); selection.clear(); }}
                    aria-label="Auswahl aufheben"
                  >
                    <X size={12} />
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
      {region && region.width > 0 && region.height > 0 && (
        <div className="region-picker__meta">
          <span>x: {(region.x * 100).toFixed(1)}% · y: {(region.y * 100).toFixed(1)}%</span>
          <span>{(region.width * 100).toFixed(1)}% × {(region.height * 100).toFixed(1)}%</span>
        </div>
      )}
    </div>
  );
}

export type { NormalizedRegion };
