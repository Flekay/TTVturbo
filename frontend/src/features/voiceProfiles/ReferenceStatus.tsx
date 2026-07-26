import { Badge, type BadgeVariant } from "../../components/ui/Badge";
import { KNOWN_REFERENCE_STATUSES, type KnownReferenceStatus } from "./schemas";
import type { ReferenceStatus } from "./types";

const STATUS_LABEL: Record<KnownReferenceStatus, string> = {
  ACCEPTED: "Akzeptiert",
  REVIEW: "Review",
  REJECTED: "Abgelehnt",
};

const STATUS_VARIANT: Record<KnownReferenceStatus, BadgeVariant> = {
  ACCEPTED: "success",
  REVIEW: "warning",
  REJECTED: "error",
};

export const MISSING_LABEL = "Fehlend";

interface ReferenceStatusBadgeProps {
  status: ReferenceStatus | null;
}

/**
 * Renders a reference status as a coloured badge **plus** an explicit text
 * label. Colour is never the only carrier of information.
 *
 * - `null` status means there is no reference yet (MISSING).
 * - Unknown status strings render as a neutral badge so future backend
 *   statuses do not crash the UI.
 */
export function ReferenceStatusBadge({ status }: ReferenceStatusBadgeProps) {
  if (status === null || status === undefined) {
    return <Badge variant="muted">{MISSING_LABEL}</Badge>;
  }
  if ((KNOWN_REFERENCE_STATUSES as readonly string[]).includes(status)) {
    const known = status as KnownReferenceStatus;
    return <Badge variant={STATUS_VARIANT[known]}>{STATUS_LABEL[known]}</Badge>;
  }
  return <Badge variant="muted">{status}</Badge>;
}

export function statusLabel(status: ReferenceStatus | null): string {
  if (status === null || status === undefined) return MISSING_LABEL;
  if ((KNOWN_REFERENCE_STATUSES as readonly string[]).includes(status)) {
    return STATUS_LABEL[status as KnownReferenceStatus];
  }
  return status;
}
