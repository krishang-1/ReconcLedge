interface DonutSegment {
  label: string;
  value: number;
  color: string;
}

/** Hand-rolled SVG donut rather than a charting library: this app has
 * exactly one chart (a 2-4 category proportion), so a whole dependency
 * would be bundle-size and version risk for something stroke-dasharray
 * does just as well - and it uses the design system's colors directly
 * instead of fighting a library's palette. */
export function DonutChart({ segments, size = 96, strokeWidth = 14 }: { segments: DonutSegment[]; size?: number; strokeWidth?: number }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  let cumulativeOffset = 0;

  return (
    <div className="flex items-center gap-5">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90 shrink-0">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="currentColor" className="text-ink-text/8" strokeWidth={strokeWidth} />
        {total > 0 &&
          segments.map((seg, i) => {
            if (seg.value === 0) return null;
            const segLength = (seg.value / total) * circumference;
            const dashOffset = -cumulativeOffset;
            cumulativeOffset += segLength;
            return (
              <circle
                key={i}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={seg.color}
                strokeWidth={strokeWidth}
                strokeDasharray={`${segLength} ${circumference - segLength}`}
                strokeDashoffset={dashOffset}
              />
            );
          })}
      </svg>
      <ul className="space-y-1.5">
        {segments.map((seg, i) => (
          <li key={i} className="flex items-center gap-2 font-mono text-xs">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: seg.color }} />
            <span className="text-ink-text/60">{seg.label}</span>
            <span className="text-ink-text font-semibold">{seg.value}</span>
            {total > 0 && <span className="text-ink-text/35">({Math.round((seg.value / total) * 100)}%)</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
