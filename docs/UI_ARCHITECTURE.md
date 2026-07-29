# UI architecture

The primary navigation is deliberately limited to six areas:

- Dashboard
- Library
- Create
- Projects
- Jobs
- Settings

Backend capabilities remain independent services. The UI exposes them contextually instead of giving every service its own top-level navigation entry.

## Quick tools

Video upscale, background removal, text-driven video editing and video generation are one-shot tools under **Create**.

1. External uploads and generated outputs are registered with `lifecycle=TEMPORARY`.
2. Temporary items are hidden from the normal Library listing.
3. Downloading a result does not persist it.
4. **Save to Library** promotes the existing item to `PERSISTENT`; no second storage system or duplicate copy is created.
5. **Open in editor** promotes the result first and then creates an Edit Project.
6. Starting a capability from a Library item keeps the source persistent and promotes the completed result automatically.

Temporary items use the same asset storage and identifiers as persistent items. The only distinction is lifecycle and expiry metadata.

## Library

The Library contains only explicitly persistent media. Its contextual actions launch the same capability pages used by Create, but with the selected source prefilled and persistent-output behavior enabled.

## Projects

Projects are reserved for timeline work, multiple tracks, output sequences and version history. Edit Projects may reference only persistent Library items.

## Jobs

Long-running operations appear in one global job model, the top-bar indicator, the compact job dock and the Jobs page. Feature-specific pages may still show local progress, but cancel, retry and result access use the same backend jobs.

## Progressive disclosure

Default forms show source, preset, preview/result and the primary action. Model IDs, seeds, encoders and fine-grained processing parameters remain behind **Advanced settings**.
