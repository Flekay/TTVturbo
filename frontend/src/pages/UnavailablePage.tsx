import { Construction, type LucideIcon } from "lucide-react";
import { Badge } from "../components/ui/Badge";

export interface UnavailablePageProps {
  title: string;
  description: string;
  plannedFeatures: string[];
  icon?: LucideIcon;
}

export function UnavailablePage({
  title,
  description,
  plannedFeatures,
  icon: Icon = Construction,
}: UnavailablePageProps) {
  return (
    <div className="page">
      <div className="unavailable" role="status">
        <Icon className="unavailable__icon" aria-hidden="true" />
        <h1 className="unavailable__title">{title}</h1>
        <p className="unavailable__description">{description}</p>
        <div>
          <Badge variant="muted">Status: Noch nicht implementiert</Badge>
        </div>
        {plannedFeatures.length > 0 && (
          <ul className="unavailable__list" aria-label="Geplante Funktionen">
            {plannedFeatures.map((feature) => (
              <li key={feature} className="unavailable__list-item">
                <span aria-hidden="true">•</span>
                <span>{feature}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
