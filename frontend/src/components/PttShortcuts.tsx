import { PTT_CODES, shortcutLabel } from "../usePtt";

interface Props {
  radios: { id: string; name: string }[];
  shortcuts: string[];
  onChange: (index: number, code: string) => void;
  disabled?: boolean;
}

export function PttShortcuts({ radios, shortcuts, onChange, disabled }: Props) {
  return (
    <details className="card" style={{ padding: 12, marginBottom: 12 }}>
      <summary>PTT keyboard shortcuts</summary>
      <p className="muted">Hold a shortcut to transmit. You can also focus a PTT button and hold Space or Enter.</p>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {radios.slice(0, 9).map((radio, index) => (
          <label key={radio.id} style={{ display: "grid", gap: 4 }}>
            {radio.name}
            <select
              className="input"
              aria-label={`PTT shortcut for ${radio.name}`}
              disabled={disabled}
              value={shortcuts[index]}
              onChange={(e) => onChange(index, e.target.value)}
            >
              {PTT_CODES.map((code) => (
                <option key={code} value={code} disabled={shortcuts.some((other, i) => i !== index && other === code)}>
                  {shortcutLabel(code)}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </details>
  );
}
