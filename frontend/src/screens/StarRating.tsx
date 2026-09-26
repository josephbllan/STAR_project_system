import { AppIcon } from "../assets/AppIcon";

export function StarRating({
  value,
  onChange,
  readOnly = false,
  compact = false,
}: {
  value: number;
  onChange: (next: number) => void;
  readOnly?: boolean;
  compact?: boolean;
}) {
  const size = compact ? 16 : 20;
  return (
    <div role="radiogroup" aria-label="Rating" className="inline-flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          role="radio"
          aria-checked={value === star}
          aria-label={`${star} of 5`}
          disabled={readOnly}
          className="leading-none disabled:cursor-default"
          onClick={(event) => {
            event.stopPropagation();
            if (!readOnly) onChange(star === value ? 0 : star);
          }}
        >
          <AppIcon name="star" size={size} className={star <= value ? "" : "opacity-25 grayscale"} />
        </button>
      ))}
    </div>
  );
}
