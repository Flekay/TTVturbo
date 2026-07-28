interface AreaChartProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  fillId?: string;
  formatValue?: (value: number) => string;
  ariaLabel?: string;
}

/**
 * Lightweight SVG area + line chart for a single time-series.
 * Renders a smooth gradient-filled area with a line and an end-point dot.
 * Uses a viewBox so it scales to its container.
 */
export function AreaChart({
  data,
  width = 320,
  height = 96,
  color = "var(--color-accent)",
  fillId = "area-fill",
  formatValue,
  ariaLabel,
}: AreaChartProps) {
  const pad = 4;
  const w = width;
  const h = height;
  const innerW = w - pad * 2;
  const innerH = h - pad * 2;

  const nonEmpty = data.filter((v) => Number.isFinite(v));
  const min = nonEmpty.length ? Math.min(...nonEmpty) : 0;
  const max = nonEmpty.length ? Math.max(...nonEmpty) : 1;
  const span = max - min || 1;

  const n = data.length;
  const xFor = (i: number) => pad + (n <= 1 ? innerW : (i / (n - 1)) * innerW);
  const yFor = (v: number) => {
    if (!Number.isFinite(v)) return pad + innerH;
    return pad + innerH - ((v - min) / span) * innerH;
  };

  const points = data.map((v, i) => [xFor(i), yFor(v)] as const);
  const linePath = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(" ");
  const areaPath =
    points.length > 0
      ? `${linePath} L${xFor(n - 1).toFixed(2)} ${(pad + innerH).toFixed(2)} L${xFor(0).toFixed(
          2,
        )} ${(pad + innerH).toFixed(2)} Z`
      : "";
  const last = points[points.length - 1];
  const lastValue = nonEmpty[nonEmpty.length - 1];

  return (
    <svg
      className="area-chart"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={ariaLabel ?? "Zeitreihe"}
    >
      <defs>
        <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {areaPath && <path d={areaPath} fill={`url(#${fillId})`} stroke="none" />}
      {linePath && (
        <path d={linePath} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
      )}
      {last && (
        <circle cx={last[0]} cy={last[1]} r={3} fill={color} stroke="var(--color-bg-panel)" strokeWidth={1.5} />
      )}
      {formatValue && Number.isFinite(lastValue) && (
        <text
          x={last ? Math.min(last[0] + 6, w - 4) : w - 4}
          y={last ? Math.max(last[1] - 6, 10) : 10}
          textAnchor="end"
          className="area-chart__label"
        >
          {formatValue(lastValue)}
        </text>
      )}
    </svg>
  );
}
