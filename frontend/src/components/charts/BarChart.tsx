export interface BarItem {
  label: string;
  value: number;
  color?: string;
  hint?: string;
}

interface BarChartProps {
  bars: BarItem[];
  formatValue?: (value: number) => string;
  ariaLabel?: string;
}

/**
 * Horizontal bar chart. Each bar shows a label, a filled track proportional
 * to its value relative to the max, and a formatted value on the right.
 */
export function BarChart({ bars, formatValue, ariaLabel }: BarChartProps) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  return (
    <ul className="bar-chart" role="img" aria-label={ariaLabel ?? "Balkendiagramm"}>
      {bars.map((b) => {
        const pct = Math.max(0, Math.min(1, b.value / max)) * 100;
        return (
          <li key={b.label} className="bar-chart__row">
            <div className="bar-chart__label">
              <span>{b.label}</span>
              {b.hint && <span className="bar-chart__hint">{b.hint}</span>}
            </div>
            <div className="bar-chart__track">
              <div
                className="bar-chart__fill"
                style={{
                  width: `${pct}%`,
                  backgroundColor: b.color ?? "var(--color-accent)",
                }}
              />
            </div>
            <div className="bar-chart__value">
              {formatValue ? formatValue(b.value) : b.value}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
