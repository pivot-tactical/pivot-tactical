import type { RadioMode } from "../types";

// Plain/Cypher mode switch rendered as a rotary dial: both positions are
// always visible, with a pointer rotating to indicate the active mode
// (spec §3.4.5, §7.2.2).
export function ModeDial({
  mode,
  onToggle,
  disabled,
  size = "md",
  title,
}: {
  mode: RadioMode;
  onToggle: () => void;
  disabled?: boolean;
  size?: "md" | "sm";
  title?: string;
}) {
  return (
    <button
      type="button"
      className={`dial dial--${size} dial--${mode === "Cypher" ? "cypher" : "plain"}`}
      onClick={onToggle}
      disabled={disabled}
      title={title}
    >
      <span className="dial__label dial__label--plain">◌ PLAIN</span>
      <span className="dial__knob">
        <span className="dial__pointer" />
      </span>
      <span className="dial__label dial__label--cypher">🔒 CYPHER</span>
    </button>
  );
}
