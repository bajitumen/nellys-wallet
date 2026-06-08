import { useState } from "react";

const PALETTE = [
  "#3b82f6", "#22c55e", "#a855f7", "#ec4899", "#f97316",
  "#14b8a6", "#eab308", "#8b5cf6", "#06b6d4", "#f59e0b",
];

function djb2(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h;
}

function letterColor(name: string, primary: string | null | undefined): string {
  if (primary && primary.startsWith("#")) return primary;
  return PALETTE[djb2(name || "?") % PALETTE.length];
}

type Props = {
  name: string;
  logo?: string | null;
  primaryColor?: string | null;
};

export function InstAvatar({ name, logo, primaryColor }: Props) {
  const [imgFailed, setImgFailed] = useState(false);
  const safeName = name || "?";
  if (logo && !imgFailed) {
    return (
      <img
        className="inst-logo"
        src={`data:image/png;base64,${logo}`}
        alt={`${safeName} logo`}
        onError={() => setImgFailed(true)}
      />
    );
  }
  const initial = safeName[0].toUpperCase();
  return (
    <span
      className="inst-letter"
      style={{ background: letterColor(safeName, primaryColor) }}
      aria-label={safeName}
    >
      {initial}
    </span>
  );
}
