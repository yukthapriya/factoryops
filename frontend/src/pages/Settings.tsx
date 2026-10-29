import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { Button, Card, DefinitionList, Field, inputCls, PageHeader, QueryState } from "../components/ui";
import { DEFAULT_PREFERENCES, RANGES, usePreferences, type Preferences, type RangeKey } from "../hooks/preferences";
import { useToast } from "../hooks/toast";
import { api } from "../lib/api";
import { humanize } from "../lib/format";
import type { Scenario } from "../lib/types";

const SCENARIOS: { value: Scenario; help: string }[] = [
  { value: "mixed", help: "A little of everything" },
  { value: "normal", help: "Noise around nominal values" },
  { value: "temperature_drift", help: "Two units heat up gradually → threshold alerts" },
  { value: "vibration_spike", help: "Periodic vibration bursts" },
  { value: "shutdown", help: "Fault, then stopped → critical + offline" },
  { value: "out_of_range", help: "Physically impossible sensor values → rejected" },
  { value: "missing_fields", help: "Required fields dropped → rejected" },
  { value: "duplicates", help: "Redelivered messages → ignored, counted" },
  { value: "out_of_order", help: "Delayed messages → stored, flagged late" },
  { value: "defects", help: "High inline-inspection failure rate" },
];

export function SettingsPage() {
  const { prefs, update, reset } = usePreferences();
  return (
    <>
      <PageHeader title="Settings" description="Display and refresh preferences are stored in this browser only. System configuration is set through environment variables on the server." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Display preferences">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Timestamps" htmlFor="pref-tz">
              <select id="pref-tz" className={inputCls} value={prefs.timeZone} onChange={(e) => update({ timeZone: e.target.value as Preferences["timeZone"] })}>
                <option value="local">Local time zone</option>
                <option value="utc">UTC</option>
              </select>
            </Field>
            <Field label="Table density" htmlFor="pref-density">
              <select id="pref-density" className={inputCls} value={prefs.density} onChange={(e) => update({ density: e.target.value as Preferences["density"] })}>
                <option value="comfortable">Comfortable</option>
                <option value="compact">Compact</option>
              </select>
            </Field>
            <Field label="Default time range" htmlFor="pref-range">
              <select id="pref-range" className={inputCls} value={prefs.defaultRange} onChange={(e) => update({ defaultRange: e.target.value as RangeKey })}>
                {RANGES.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Your name (for audit notes)" htmlFor="pref-name" hint="Prefills acknowledge/resolve and investigation forms. Not authentication.">
              <input id="pref-name" className={inputCls} value={prefs.operatorName} maxLength={80} onChange={(e) => update({ operatorName: e.target.value })} />
            </Field>
          </div>
        </Card>

        <Card title="Telemetry refresh">
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={prefs.liveUpdates} onChange={(e) => update({ liveUpdates: e.target.checked })} />
              Live updates over WebSocket
            </label>
            <Field label="Polling fallback" htmlFor="pref-poll" hint="Used when live updates are off or the connection is down.">
              <select id="pref-poll" className={inputCls} value={prefs.pollingSeconds} onChange={(e) => update({ pollingSeconds: Number(e.target.value) as Preferences["pollingSeconds"] })}>
                <option value={0}>Off (manual refresh)</option>
                <option value={15}>Every 15 s</option>
                <option value={30}>Every 30 s</option>
                <option value={60}>Every 60 s</option>
              </select>
            </Field>
            <Button variant="ghost" onClick={reset} disabled={JSON.stringify(prefs) === JSON.stringify(DEFAULT_PREFERENCES)}>
              Reset preferences to defaults
            </Button>
          </div>
        </Card>

        <DemoControls />
        <ServerPolicy />
      </div>
    </>
  );
}

function DemoControls() {
  const qc = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: ["settings", "public"], queryFn: api.publicSettings, staleTime: 60_000 });
  const [scenario, setScenario] = useState<Scenario>("mixed");
  const [ticks, setTicks] = useState(30);
  const [seed, setSeed] = useState("");
  const enabled = settings.data?.demo_controls_enabled ?? false;
  const ticksValid = Number.isInteger(ticks) && ticks >= 1 && ticks <= 300;

  const simulate = useMutation({
    mutationFn: () => api.simulate({ scenario, ticks, seed: seed === "" ? undefined : Number(seed) }),
    onSuccess: (r) => {
      void qc.invalidateQueries();
      toast.success(
        `Injected ${r.events_sent} events (${humanize(r.scenario)})`,
        Object.entries(r.outcomes)
          .map(([k, v]) => `${humanize(k)}: ${v}`)
          .join(" · "),
      );
    },
    onError: (err) => toast.error("Simulation failed", err),
  });
  const baselines = useMutation({
    mutationFn: () => api.recomputeBaselines(6),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["analytics"] });
      toast.success(`Recomputed ${r.baselines_updated} SPC baselines`);
    },
    onError: (err) => toast.error("Baseline recompute failed", err),
  });

  return (
    <Card title="Demo data" description="Push a synthetic scenario through the real validation, dedup and alerting pipeline (in-process, no Kafka needed).">
      {settings.isPending ? (
        <p className="text-sm text-ink-3">Checking server settings…</p>
      ) : !enabled ? (
        <p className="text-sm text-ink-3">
          Demo controls are disabled on this server (FACTORYOPS_DEMO_CONTROLS_ENABLED=false). Use the telemetry simulator CLI instead.
        </p>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Scenario" htmlFor="demo-scenario" hint={SCENARIOS.find((s) => s.value === scenario)?.help}>
              <select id="demo-scenario" className={inputCls} value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>
                {SCENARIOS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {humanize(s.value)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Ticks (1–300)" htmlFor="demo-ticks" hint="One event per unit per tick (15 units)" error={ticksValid ? undefined : "Enter a whole number from 1 to 300"}>
              <input id="demo-ticks" type="number" min={1} max={300} className={inputCls} value={ticks} aria-invalid={!ticksValid} onChange={(e) => setTicks(Number(e.target.value))} />
            </Field>
            <Field label="Seed (optional)" htmlFor="demo-seed" hint="Same seed → same events (replays are deduplicated)">
              <input id="demo-seed" type="number" min={0} className={inputCls} value={seed} onChange={(e) => setSeed(e.target.value)} />
            </Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => simulate.mutate()} disabled={simulate.isPending || !ticksValid}>
              {simulate.isPending ? "Running…" : "Run scenario"}
            </Button>
            <Button onClick={() => baselines.mutate()} disabled={baselines.isPending}>
              {baselines.isPending ? "Recomputing…" : "Recompute SPC baselines (last 6 h)"}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function ServerPolicy() {
  const q = useQuery({ queryKey: ["settings", "public"], queryFn: api.publicSettings, staleTime: 60_000 });
  return (
    <Card title="Server policy (read-only)" description="Non-secret values from the server environment.">
      <QueryState query={q}>
        {(s) => (
          <>
            <DefinitionList
              items={[
                ["Environment", s.environment],
                ["Late after", `${s.late_event_seconds} s`],
                ["Max future clock skew", `${s.max_clock_skew_seconds} s`],
                ["Max event age", `${Math.round(s.max_event_age_seconds / 3600)} h`],
                ["Alert cooldown", `${s.alert_cooldown_seconds} s`],
                ["Transient retry limit", `${s.processing_max_retries}`],
              ]}
            />
            <p className="mt-3 text-xs text-ink-3">
              Per-equipment alert thresholds are configured on each unit — open it from the <Link to="/equipment" className="text-accent hover:underline">Equipment</Link> page and choose Edit.
            </p>
          </>
        )}
      </QueryState>
    </Card>
  );
}
