import { useMemo, useState } from "react";

type DevicePreference = "auto" | "NPU" | "GPU" | "CPU";
type Language = "auto" | "en" | "vi";

const devices: Array<{ value: DevicePreference; label: string; description: string }> = [
  { value: "auto", label: "Auto", description: "Prefer NPU, then GPU, then CPU" },
  { value: "NPU", label: "Intel NPU", description: "Require OpenVINO NPU availability" },
  { value: "GPU", label: "GPU", description: "Use a compatible local GPU runtime" },
  { value: "CPU", label: "CPU", description: "Use the CPU fallback" },
];

export function App() {
  const [sourceName, setSourceName] = useState<string | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState<Language>("auto");
  const [outputLanguage, setOutputLanguage] = useState<Language>("vi");
  const [device, setDevice] = useState<DevicePreference>("auto");
  const canStart = sourceName !== null;

  const deviceDescription = useMemo(
    () => devices.find((item) => item.value === device)?.description ?? "",
    [device],
  );

  return (
    <main className="app-shell">
      <header className="header">
        <div>
          <p className="eyebrow">LOCAL · PRIVATE · WINDOWS 11</p>
          <h1>LocalSub NPU</h1>
          <p className="subtitle">Offline subtitle generation with local models and Intel NPU preference.</p>
        </div>
        <span className="status-chip" aria-label="Application status">Wave 1 scaffold</span>
      </header>

      <section className="grid" aria-label="New subtitle job">
        <article className="panel import-panel">
          <h2>1. Import media</h2>
          <p>Choose a local video or audio file. Source media remains on this device.</p>
          <label className="drop-zone" htmlFor="media-file">
            <span className="drop-title">{sourceName ?? "Browse video or audio"}</span>
            <span className="drop-hint">MP4, MKV, MOV, AVI, WEBM, MP3, WAV</span>
          </label>
          <input
            id="media-file"
            type="file"
            accept="video/*,audio/*"
            onChange={(event) => setSourceName(event.target.files?.[0]?.name ?? null)}
          />
          {sourceName && <p className="success" role="status">Selected: {sourceName}</p>}
        </article>

        <article className="panel">
          <h2>2. Configure</h2>
          <div className="field-group">
            <label htmlFor="source-language">Spoken language</label>
            <select
              id="source-language"
              value={sourceLanguage}
              onChange={(event) => setSourceLanguage(event.target.value as Language)}
            >
              <option value="auto">Auto detect</option>
              <option value="en">English</option>
              <option value="vi">Vietnamese</option>
            </select>
          </div>
          <div className="field-group">
            <label htmlFor="output-language">Subtitle language</label>
            <select
              id="output-language"
              value={outputLanguage}
              onChange={(event) => setOutputLanguage(event.target.value as Language)}
            >
              <option value="en">English</option>
              <option value="vi">Vietnamese</option>
              <option value="auto">Same as source / auto</option>
            </select>
          </div>
          <div className="field-group">
            <label htmlFor="device">Inference device</label>
            <select
              id="device"
              value={device}
              onChange={(event) => setDevice(event.target.value as DevicePreference)}
            >
              {devices.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <p className="help-text">{deviceDescription}</p>
          </div>
        </article>

        <article className="panel diagnostics-panel">
          <h2>3. Runtime status</h2>
          <dl>
            <div><dt>Requested device</dt><dd>{device}</dd></div>
            <div><dt>Actual device</dt><dd>Not probed</dd></div>
            <div><dt>FFmpeg</dt><dd>Not checked</dd></div>
            <div><dt>OpenVINO</dt><dd>Not checked</dd></div>
          </dl>
          <p className="help-text">Run the Python diagnostics command before processing media.</p>
          <code>python -m app.main diagnostics</code>
        </article>
      </section>

      <section className="panel action-panel" aria-label="Start processing">
        <div>
          <h2>Generate subtitle</h2>
          <p>Wave 1 validates UI flow only. FFmpeg extraction and Whisper inference arrive in the next vertical slice.</p>
        </div>
        <button type="button" disabled={!canStart} onClick={() => window.alert("Wave 1: job execution is not implemented yet.")}>
          Start local job
        </button>
      </section>
    </main>
  );
}
