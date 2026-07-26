import { Link } from "react-router-dom";
import { Compass } from "lucide-react";

export function NotFoundPage() {
  return (
    <div className="page">
      <div className="unavailable" role="status">
        <Compass className="unavailable__icon" aria-hidden="true" />
        <h1 className="unavailable__title">Seite nicht gefunden</h1>
        <p className="unavailable__description">
          Die angeforderte Route existiert nicht. Zurück zum Dashboard.
        </p>
        <Link className="btn btn--primary" to="/dashboard">
          Zum Dashboard
        </Link>
      </div>
    </div>
  );
}
