import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { BarList, PrCurveChart, SERIES } from "../components/charts";
import { DataTable, type Column } from "../components/DataTable";
import { Card, DefinitionList, EmptyState, ErrorState, FilterBar, KpiCard, LoadingBlock, PageHeader, QueryState, SelectFilter, StatusBadge } from "../components/ui";
import { useSearchState } from "../hooks/useSearchState";
import { api, ApiError } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct } from "../lib/format";
import { HISTORICAL_FEATURES, type Ai4iRecord, type HistoricalFeature, type ModelEvaluation } from "../lib/types";

const MODEL_LABEL: Record<string, string> = {
  logistic_regression: "Logistic regression",
  gradient_boosting: "Gradient boosting",
  documented_rules: "Documented rules (not trained)",
};
const MODE_HELP: Record<string, string> = {
  TWF: "Tool wear failure",
  HDF: "Heat dissipation failure",
  PWF: "Power failure",
  OSF: "Overstrain failure",
  RNF: "Random failure",
};
const PAGE_SIZE = 20;

export function HistoricalPage() {
  const summary = useQuery({ queryKey: ["historical", "summary"], queryFn: api.historicalSummary, staleTime: 5 * 60_000 });

  if (summary.isPending) return <LoadingBlock rows={8} label="Loading historical benchmark" />;
  if (summary.isError) {
    if (summary.error instanceof ApiError && summary.error.status === 404) return <NotImported />;
    return <ErrorState error={summary.error} onRetry={() => summary.refetch()} />;
  }
  const s = summary.data;
  const obs = s.dataset.observations;

  return (
    <>
      <PageHeader
        title="Historical failure analysis"
        description="Which operating conditions precede milling-process failures, and how well can a failure be flagged from process measurements alone? Answered on a documented public benchmark, separate from live telemetry."
      />
      <div role="note" className="mb-4 rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
        <strong>Historical benchmark, not live data.</strong> {s.dataset.title} is a <em>synthetic</em> dataset published by UCI that reflects real predictive-maintenance data. It is not
        telemetry from this plant's simulator and not data from any specific manufacturer. It is stored in a separate <code className="font-mono">historical</code> database schema.
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard label="Process records" value={fmtNum(s.rows)} sub={`${s.dataset.file_name}`} />
        <KpiCard label="Machine failures" value={fmtNum(s.failures)} tone="critical" sub="Machine failure = 1" />
        <KpiCard label="Failure rate" value={fmtPct(s.failure_rate_pct)} sub="failures ÷ records" />
        {s.by_type.map((t) => (
          <KpiCard key={t.product_type} label={`Type ${t.product_type} failure rate`} value={fmtPct(t.failure_rate_pct)} sub={`${fmtNum(t.failures)} of ${fmtNum(t.rows)} (quality ${t.product_type === "L" ? "low" : t.product_type === "M" ? "medium" : "high"})`} />
        ))}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card title="Data provenance" className="xl:col-span-2">
          <DefinitionList
            items={[
              ["Source", <a href={s.dataset.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{s.dataset.title} (UCI Machine Learning Repository)</a>],
              ["Citation", s.dataset.citation],
              ["DOI", s.dataset.doi ?? "—"],
              ["License", s.dataset.license],
              ["File", s.dataset.file_name],
              ["SHA-256", <span className="font-mono text-xs break-all">{s.dataset.file_sha256}</span>],
              ["Checksum", obs.matches_published_checksum ? "matches the pinned UCI file" : "differs from the pinned UCI file (not the published version)"],
              ["Rows imported", `${fmtNum(s.dataset.row_count)} (all-or-nothing validated import)`],
              ["Imported", fmtDateTime(s.dataset.imported_at)],
            ]}
          />
        </Card>

        <Card title="Failure modes" description="A row can carry more than one mode">
          <BarList label="Count of each failure-mode label" valueLabel="Rows" data={s.by_mode.map((m) => ({ name: m.key, value: m.count, detail: MODE_HELP[m.key] }))} />
          <h3 className="mt-3 mb-1 text-xs font-semibold text-ink-2">Label observations in the source file</h3>
          <ul className="space-y-0.5 text-xs text-ink-3">
            <li>{obs.failure_without_mode} failures carry no failure-mode flag</li>
            {Object.entries(obs.mode_without_failure)
              .filter(([, v]) => v > 0)
              .map(([k, v]) => (
                <li key={k}>
                  {v} rows flag {k} while Machine failure = 0
                </li>
              ))}
            <li>{obs.multi_mode_rows} rows carry more than one mode</li>
          </ul>
          <p className="mt-2 text-xs text-ink-3">Reported as found; the importer does not alter labels.</p>
        </Card>

        <FeatureAnalysis />
        <RuleChecks />
      </div>

      <ModelEvaluations />
      <RecordsTable />
    </>
  );
}

function NotImported() {
  return (
    <>
      <PageHeader title="Historical failure analysis" description="UCI AI4I 2020 Predictive Maintenance Dataset (synthetic benchmark)." />
      <EmptyState title="The AI4I 2020 dataset has not been imported yet">
        <p>Download, validate, import and evaluate it with one command:</p>
        <pre className="mt-2 rounded bg-page p-2 text-left font-mono text-[0.7rem] text-ink-2">
          {"cd backend && uv run python -m factoryops.cli ai4i-setup\n# Docker: docker compose run --rm api python -m factoryops.cli ai4i-setup"}
        </pre>
      </EmptyState>
    </>
  );
}

function FeatureAnalysis() {
  const [feature, setFeature] = useState<HistoricalFeature>("tool_wear_min");
  const [bins, setBins] = useState(12);
  const q = useQuery({ queryKey: ["historical", "analysis", feature, bins], queryFn: () => api.failureAnalysis(feature, bins), placeholderData: keepPreviousData, staleTime: 5 * 60_000 });
  return (
    <Card
      className="xl:col-span-2"
      title="Failure rate by operating condition"
      description="Equal-width bins over the full range; rate = failures ÷ records in the bin"
      actions={
        <div className="flex flex-wrap gap-2">
          <SelectFilter label="Variable" value={feature} allLabel="" onChange={(v) => setFeature(v as HistoricalFeature)} options={HISTORICAL_FEATURES} />
          <SelectFilter label="Bins" value={String(bins)} allLabel="" onChange={(v) => setBins(Number(v))} options={["8", "12", "20", "30"].map((b) => ({ value: b, label: b }))} />
        </div>
      }
    >
      <QueryState query={q}>
        {(a) => (
          <BarList
            label={`Failure rate by ${a.label}`}
            layout="horizontal"
            height={260}
            valueLabel="Failure rate"
            format={(v) => `${v.toFixed(1)}%`}
            caption={`${a.label} (${a.unit})${a.derived ? ", derived from dataset columns as described by UCI" : ""}. Hover a bar for record counts; sparse bins have wide uncertainty.`}
            data={a.bins.map((b) => ({
              name: `${fmtNum(b.lower, 1)}–${fmtNum(b.upper, 1)}`,
              value: b.failure_rate_pct ?? 0,
              detail: `${b.failures} / ${fmtNum(b.rows)} records${Object.entries(b.modes).filter(([, v]) => v).length ? " · " + Object.entries(b.modes).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(", ") : ""}`,
            }))}
          />
        )}
      </QueryState>
    </Card>
  );
}

function RuleChecks() {
  const q = useQuery({ queryKey: ["historical", "rules"], queryFn: api.failureRules, staleTime: 5 * 60_000 });
  return (
    <Card title="Documented failure rules vs labels" description="Rules from the UCI dataset description, checked against this file">
      <QueryState query={q}>
        {(rules) => (
          <div className="space-y-3">
            {rules.map((r) => (
              <div key={r.mode} className="rounded border border-line p-2 text-xs">
                <p className="flex items-center gap-2">
                  <strong className="text-sm text-ink">{r.mode}</strong>
                  <span className="text-ink-3">{MODE_HELP[r.mode]}</span>
                  <span className="ml-auto">
                    <StatusBadge value={r.deterministic ? "deterministic" : "random in generator"} tone={r.deterministic ? "info" : "neutral"} />
                  </span>
                </p>
                <p className="mt-1 text-ink-2">{r.rule}</p>
                <p className="tabular mt-1 text-ink-3">
                  rule true {fmtNum(r.rule_matches)} · labeled {fmtNum(r.labeled)} · agree {r.true_positive} · rule-only {r.false_positive} · label-only {r.false_negative}
                </p>
              </div>
            ))}
          </div>
        )}
      </QueryState>
    </Card>
  );
}

function ConfusionMatrix({ cm }: { cm: ModelEvaluation["metrics"]["confusion_matrix"] }) {
  const cell = "tabular border border-line px-2 py-1 text-right";
  return (
    <table className="text-xs">
      <caption className="sr-only">Confusion matrix on the test set</caption>
      <thead>
        <tr>
          <th />
          <th scope="col" className="px-2 py-1 font-medium text-ink-3">Pred. OK</th>
          <th scope="col" className="px-2 py-1 font-medium text-ink-3">Pred. fail</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row" className="pr-2 text-left font-medium text-ink-3">Actual OK</th>
          <td className={cell} title="true negatives">{fmtNum(cm.tn)}</td>
          <td className={cell} title="false positives (false alarms)">{fmtNum(cm.fp)}</td>
        </tr>
        <tr>
          <th scope="row" className="pr-2 text-left font-medium text-ink-3">Actual fail</th>
          <td className={cell} title="false negatives (missed failures)">{fmtNum(cm.fn)}</td>
          <td className={cell} title="true positives">{fmtNum(cm.tp)}</td>
        </tr>
      </tbody>
    </table>
  );
}

function ModelEvaluations() {
  const q = useQuery({ queryKey: ["historical", "evaluations"], queryFn: api.modelEvaluations, staleTime: 5 * 60_000 });
  return (
    <section className="mt-6" aria-labelledby="model-eval-heading">
      <h2 id="model-eval-heading" className="mb-1 text-base font-semibold">
        Failure prediction: offline evaluation
      </h2>
      <p className="mb-3 max-w-4xl text-sm text-ink-3">
        Target is <code className="font-mono">Machine failure</code>. Inputs are only product type and the five process measurements. The failure-mode labels and the identifiers are excluded so
        the model cannot see the answer. Metrics are measured on a held-out stratified test split.
      </p>
      <QueryState query={q} isEmpty={(d) => d.length === 0} empty={<EmptyState title="No evaluation stored">Run <code className="font-mono">python -m factoryops.cli ai4i-evaluate</code>.</EmptyState>}>
        {(evals) => {
          const split = evals[0]!.params.split;
          return (
            <div className="grid gap-4 xl:grid-cols-3">
              <Card className="xl:col-span-3" title="Test-set metrics" description={`Decision threshold ${evals[0]!.metrics.threshold} on predicted probability · PR-AUC = average precision (threshold-free)`}>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <caption className="sr-only">Test-set metrics per model</caption>
                    <thead className="text-left text-xs text-ink-3">
                      <tr>
                        <th className="py-1 pr-3 font-medium">Model</th>
                        <th className="py-1 pr-3 text-right font-medium">Precision</th>
                        <th className="py-1 pr-3 text-right font-medium">Recall</th>
                        <th className="py-1 pr-3 text-right font-medium">F1</th>
                        <th className="py-1 pr-3 text-right font-medium">PR-AUC</th>
                        <th className="py-1 pr-3 font-medium">Confusion matrix</th>
                        <th className="py-1 font-medium">Failures caught by mode</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evals.map((e) => (
                        <tr key={e.model_name} className="border-t border-line align-top">
                          <td className="py-2 pr-3">
                            <p className="font-medium">{MODEL_LABEL[e.model_name] ?? e.model_name}</p>
                            <p className="max-w-xs text-xs text-ink-3">{e.description}</p>
                          </td>
                          <td className="tabular py-2 pr-3 text-right">{e.metrics.precision.toFixed(3)}</td>
                          <td className="tabular py-2 pr-3 text-right">{e.metrics.recall.toFixed(3)}</td>
                          <td className="tabular py-2 pr-3 text-right">{e.metrics.f1.toFixed(3)}</td>
                          <td className="tabular py-2 pr-3 text-right">{e.params.trained ? e.metrics.pr_auc.toFixed(3) : "n/a"}</td>
                          <td className="py-2 pr-3">
                            <ConfusionMatrix cm={e.metrics.confusion_matrix} />
                          </td>
                          <td className="tabular py-2 text-xs text-ink-2">
                            {Object.entries(e.metrics.recall_by_mode).map(([m, v]) => (
                              <p key={m}>
                                {m}: {v.detected}/{v.failures}
                              </p>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card className="xl:col-span-2" title="Precision–recall curves (test set)">
                <PrCurveChart
                  label="Precision-recall curves of the trained models on the test set"
                  baseline={split.test_prevalence}
                  caption="Each point is a different decision threshold. A model that guesses scores the dashed prevalence line."
                  curves={evals
                    .filter((e) => e.params.trained)
                    .map((e, i) => ({ name: MODEL_LABEL[e.model_name] ?? e.model_name, color: SERIES[i] ?? SERIES[0], points: e.pr_curve }))}
                />
              </Card>

              <Card title="Evaluation protocol">
                <DefinitionList
                  items={[
                    ["Split", `${split.method}, seed ${split.seed}, test size ${split.test_size}`],
                    ["Train", `${fmtNum(split.train_rows)} rows, ${split.train_positives} failures (${(split.train_prevalence * 100).toFixed(2)}%)`],
                    ["Test", `${fmtNum(split.test_rows)} rows, ${split.test_positives} failures (${(split.test_prevalence * 100).toFixed(2)}%)`],
                    ["Features", evals[0]!.params.features.join(", ")],
                    ["Excluded", evals[0]!.params.excluded_columns.join(", ")],
                    ["Libraries", Object.entries(evals[0]!.params.library_versions).map(([k, v]) => `${k} ${v}`).join(", ")],
                    ["Data SHA-256", <span className="font-mono text-xs break-all">{evals[0]!.params.dataset_sha256?.slice(0, 16)}…</span>],
                  ]}
                />
                <h3 className="mt-3 mb-1 text-xs font-semibold text-ink-2">Gradient boosting: permutation importance</h3>
                <BarList
                  label="Drop in test PR-AUC when each input is shuffled (gradient boosting)"
                  valueLabel="PR-AUC drop"
                  allowDecimals
                  format={(v) => v.toFixed(3)}
                  data={(evals.find((e) => e.model_name === "gradient_boosting")?.feature_importance ?? []).map((f) => ({ name: f.feature, value: Math.max(0, f.mean), detail: `±${f.std.toFixed(3)}` }))}
                  caption="Mean over 5 shuffles of the test set."
                />
              </Card>
            </div>
          );
        }}
      </QueryState>
    </section>
  );
}

function RecordsTable() {
  const [f, setF, reset] = useSearchState({ type: "", failure: "", mode: "", page: "1" });
  const page = Number(f.page) || 1;
  const params = { product_type: f.type, failure: f.failure, mode: f.mode, page, page_size: PAGE_SIZE };
  const q = useQuery({ queryKey: ["historical", "records", params], queryFn: () => api.historicalRecords(params), placeholderData: keepPreviousData });
  const flags = (r: Ai4iRecord) => (["twf", "hdf", "pwf", "osf", "rnf"] as const).filter((m) => r[m]).map((m) => m.toUpperCase()).join(", ") || "—";
  const columns: Column<Ai4iRecord>[] = [
    { key: "udi", header: "UDI", numeric: true, render: (r) => r.udi },
    { key: "pid", header: "Product", render: (r) => <span className="font-mono text-xs">{r.product_id}</span> },
    { key: "air", header: "Air K", numeric: true, render: (r) => r.air_temp_k.toFixed(1) },
    { key: "proc", header: "Process K", numeric: true, render: (r) => r.process_temp_k.toFixed(1) },
    { key: "rpm", header: "rpm", numeric: true, render: (r) => fmtNum(r.rotational_speed_rpm) },
    { key: "torque", header: "Torque Nm", numeric: true, render: (r) => r.torque_nm.toFixed(1) },
    { key: "wear", header: "Wear min", numeric: true, render: (r) => r.tool_wear_min },
    { key: "power", header: "Power W", numeric: true, render: (r) => fmtNum(r.power_w) },
    { key: "fail", header: "Failure", render: (r) => (r.machine_failure ? <StatusBadge value="failure" tone="critical" /> : <span className="text-ink-3">no</span>) },
    { key: "modes", header: "Modes", render: (r) => <span className="text-xs">{flags(r)}</span> },
  ];
  return (
    <section className="mt-6" aria-labelledby="records-heading">
      <h2 id="records-heading" className="mb-2 text-base font-semibold">
        Records
      </h2>
      <FilterBar onReset={reset}>
        <SelectFilter label="Type" value={f.type} onChange={(v) => setF({ type: v })} options={["L", "M", "H"].map((t) => ({ value: t, label: t }))} />
        <SelectFilter label="Failure" value={f.failure} onChange={(v) => setF({ failure: v })} options={[{ value: "true", label: "Failed" }, { value: "false", label: "No failure" }]} />
        <SelectFilter label="Mode" value={f.mode} onChange={(v) => setF({ mode: v })} options={Object.keys(MODE_HELP).map((m) => ({ value: m, label: `${m} · ${MODE_HELP[m]}` }))} />
      </FilterBar>
      <DataTable
        caption="AI4I 2020 records"
        columns={columns}
        rows={q.data?.items}
        rowKey={(r) => String(r.udi)}
        loading={q.isPending}
        fetching={q.isFetching && !q.isPending}
        error={q.isError ? q.error : null}
        onRetry={() => q.refetch()}
        emptyTitle="No records match"
        page={page}
        pageSize={PAGE_SIZE}
        total={q.data?.total}
        onPage={(p) => setF({ page: String(p) })}
      />
    </section>
  );
}
