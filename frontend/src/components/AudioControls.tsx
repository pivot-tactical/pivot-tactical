import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AudioIO } from "../audio";
import "./AudioControls.css";

export function useMicState(audio: AudioIO) {
  return useSyncExternalStore(audio.onMicState, audio.getMicState, audio.getMicState);
}

export function AudioControls({ audio, disabled = false }: { audio: AudioIO; disabled?: boolean }) {
  const state = useMicState(audio);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceError, setDeviceError] = useState(false);
  const meter = useRef<HTMLMeterElement>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      if (!navigator.mediaDevices?.enumerateDevices) return;
      try {
        const found = await navigator.mediaDevices.enumerateDevices();
        if (!cancelled) {
          setDevices(found.filter((device) => device.kind === "audioinput" && device.deviceId !== "default"));
          setDeviceError(false);
        }
      } catch { if (!cancelled) setDeviceError(true); }
    };
    void refresh();
    navigator.mediaDevices?.addEventListener?.("devicechange", refresh);
    return () => {
      cancelled = true;
      navigator.mediaDevices?.removeEventListener?.("devicechange", refresh);
    };
  }, [state.status]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (meter.current) meter.current.value = audio.getInputLevel();
    }, 80);
    return () => window.clearInterval(timer);
  }, [audio]);

  const retry = () => { void audio.prewarm().catch(() => {}); };
  const opening = state.status === "opening";
  return (
    <section className="audio-controls" aria-label="Microphone setup">
      <div className="audio-controls__status" role="status" aria-live="polite">
        <strong>{state.status === "ready" ? "Microphone ready" : opening ? "Opening microphone…" : state.status === "error" ? "Microphone unavailable" : "Microphone not checked"}</strong>
        {state.error && <span className="audio-controls__error">{state.error}</span>}
      </div>
      <div className="audio-controls__row">
        <label className="audio-controls__device">
          Microphone
          <select className="input" aria-label="Microphone device" value={state.deviceId}
            disabled={disabled || opening}
            onChange={(event) => { void audio.setInputDevice(event.target.value).catch(() => {}); }}>
            <option value="">System default</option>
            {state.deviceId && !devices.some((device) => device.deviceId === state.deviceId) && <option value={state.deviceId}>Previously selected microphone</option>}
            {devices.map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Microphone ${index + 1}`}</option>)}
          </select>
        </label>
        <label className="audio-controls__level">
          Input level
          <meter ref={meter} min={0} max={1} value={0} aria-label="Microphone input level" />
          <small>Speak to check your level.</small>
        </label>
        <button className="btn" disabled={disabled || opening} onClick={retry}>
          {state.status === "error" ? "Retry microphone" : "Test microphone"}
        </button>
      </div>
      {deviceError && <p className="audio-controls__error">Could not list microphones. Check browser permissions and retry the microphone test.</p>}
    </section>
  );
}
