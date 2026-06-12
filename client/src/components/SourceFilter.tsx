import { useSearchParams } from "react-router-dom";
import { InstAvatar } from "./InstAvatar";

type Props = {
  sources: string[];
  current: string | null;
  logos?: Record<string, { logo?: string | null; primary_color?: string | null }>;
};

export function SourceFilter({ sources, current, logos = {} }: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  if (sources.length === 0) return null;

  function pick(value: string | null) {
    const p = new URLSearchParams(searchParams);
    if (value === null) p.delete("source");
    else p.set("source", value);
    setSearchParams(p);
  }

  return (
    <div className="source-filter">
      <a
        href="#"
        className={!current ? "active" : undefined}
        onClick={(e) => {
          e.preventDefault();
          pick(null);
        }}
      >
        All
      </a>
      {sources.map((src) => {
        const meta = logos[src] ?? {};
        return (
          <a
            key={src}
            href="#"
            className={current === src ? "active" : undefined}
            onClick={(e) => {
              e.preventDefault();
              pick(src);
            }}
          >
            <InstAvatar name={src} logo={meta.logo} primaryColor={meta.primary_color} />
            {src}
          </a>
        );
      })}
    </div>
  );
}
