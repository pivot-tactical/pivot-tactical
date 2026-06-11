// Per-radio headset volume control (spec §3.2.2): every radio — instructor and
// trainee — can individually set how loud its received audio plays into the
// operator's headset. The value is a 0–1 gain applied to that radio's playback.

export function VolumeSlider({
  value,
  onChange,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  const pct = Math.round(value * 100);
  return (
    <div className="volume">
      <span className="volume__label">VOLUME · {pct}%</span>
      <input
        className="volume__range"
        type="range"
        min={0}
        max={100}
        step={1}
        value={pct}
        disabled={disabled}
        aria-label="Headset volume"
        onChange={(e) => onChange(Number(e.target.value) / 100)}
      />
    </div>
  );
}
