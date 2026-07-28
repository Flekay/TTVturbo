export interface DonutSegment {
  label: string;
  value: number;
  color: string;
}

interface DonutChartProps {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
  centerLabel?: string;
  centerSub?: string;
  ariaLabel?: string;
}

/**
 * SVG donut chart. Each segment is drawn as a stroked arc using
 * stroke-dasharray. The center can show a primary label + sub label.
 */
export function DonutChart({
  segments,
  size = 160,
  thickness = 16,
  centerLabel,
  centerSub,
  ariaLabel,
}: DonutChartProps) {
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0) || 1;
  const center = size / 2;

  let offset = 0;
  const arcs = segments
    .filter((s) => s.value > 0)
    .map((s) => {
      const fraction = s.value / total;
      const dash = fraction * circumference;
      const arc = {
        color: s.color,
        dash,
        gap: circumference - dash,
        offset: -offset,
        label: s.label,
        value: s.value,
        fraction,
      };
      offset += dash;
      return arc;
    });

  return (
    <svg
      className="donut-chart"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={ariaLabel ?? "Verteilung"}
    >
      <circle
        cx={center}
        cy={center}
        r={radius}
        fill="none"
        stroke="var(--color-bg-elevated)"
        strokeWidth={thickness}
      />
      {arcs.map((arc, i) => (
        <circle
          key={i}
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={arc.color}
          strokeWidth={thickness}
          strokeDasharray={`${arc.dash} ${arc.gap}`}
          strokeDashoffset={arc.offset}
          strokeLinecap="butt"
          transform={`rotate(-90 ${center} ${center})`}
        />
      ))}
      {centerLabel && (
        <text
          x={center}
          y={center - 2}
          textAnchor="middle"
          dominantBaseline="middle"
          className="donut-chart__center"
        >
          {centerLabel}
        </text>
      )}
      {centerSub && (
        <text
          x={center}
          y={center + 14}
          textAnchor="middle"
          dominantBaseline="middle"
          className="donut-chart__center-sub"
        >
          {centerSub}
        </text>
      )}
    </svg>
  );
}
