type Option = { value: string; label: string };

type Props = {
  options: Option[];
  selected: string[];
  onToggle: (value: string) => void;
  ariaLabel?: string;
};

export function FilterChips({ options, selected, onToggle, ariaLabel }: Props) {
  if (options.length === 0) return null;
  return (
    <div className="tx-filter-chips" role="group" aria-label={ariaLabel}>
      {options.map((o) => {
        const active = selected.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            className={`filter-chip${active ? " filter-chip-active" : ""}`}
            onClick={() => onToggle(o.value)}
          >
            {o.label}
            {active && <span className="filter-chip-x"> ×</span>}
          </button>
        );
      })}
    </div>
  );
}
