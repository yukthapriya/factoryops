import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useToast } from "../hooks/toast";
import { api, ApiError } from "../lib/api";
import { EQUIPMENT_STATUSES, METRICS, METRIC_META, type Equipment, type Metric, type Threshold } from "../lib/types";
import { Button, Field, inputCls, Modal } from "./ui";

// Mirrors backend PLAUSIBLE_RANGES (factoryops/domain.py). The API re-validates everything.
export const PLAUSIBLE: Record<Metric, [number, number]> = {
  temperature: [-40, 250],
  vibration: [0, 100],
  pressure: [0, 20],
  cycle_time: [0, 600],
};

const optionalNumber = z
  .union([z.string(), z.number(), z.null(), z.undefined()])
  .transform((v) => (v === "" || v === null || v === undefined ? null : Number(v)))
  .refine((v) => v === null || Number.isFinite(v), "Must be a number");

const thresholdSchema = z
  .object({
    metric: z.enum(["temperature", "vibration", "pressure", "cycle_time"]),
    warning_low: optionalNumber,
    warning_high: optionalNumber,
    critical_low: optionalNumber,
    critical_high: optionalNumber,
  })
  .superRefine((t, ctx) => {
    const [lo, hi] = PLAUSIBLE[t.metric];
    for (const k of ["warning_low", "warning_high", "critical_low", "critical_high"] as const) {
      const v = t[k];
      if (v !== null && (v < lo || v > hi)) ctx.addIssue({ code: "custom", path: [k], message: `Outside plausible range ${lo}–${hi}` });
    }
    if (t.warning_high !== null && t.critical_high !== null && t.warning_high > t.critical_high)
      ctx.addIssue({ code: "custom", path: ["warning_high"], message: "Warning high must be ≤ critical high" });
    if (t.warning_low !== null && t.critical_low !== null && t.warning_low < t.critical_low)
      ctx.addIssue({ code: "custom", path: ["warning_low"], message: "Warning low must be ≥ critical low" });
    const lows = [t.warning_low, t.critical_low].filter((v): v is number => v !== null);
    const highs = [t.warning_high, t.critical_high].filter((v): v is number => v !== null);
    if (lows.length && highs.length && Math.max(...lows) >= Math.min(...highs))
      ctx.addIssue({ code: "custom", path: ["warning_low"], message: "Low limits must be below high limits" });
  });

export const equipmentSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^[A-Z0-9][A-Z0-9-]{1,31}$/, "2–32 chars: uppercase letters, digits and hyphens (e.g. PRESS-04)"),
  name: z.string().trim().min(2, "At least 2 characters").max(120),
  station: z.string().trim().min(1, "Station is required").max(64),
  equipment_type: z.string().trim().min(1, "Type is required").max(64),
  status: z.enum(["running", "idle", "warning", "critical", "offline", "maintenance"]),
  thresholds: z.array(thresholdSchema),
});

type FormInput = z.input<typeof equipmentSchema>;
type FormOutput = z.output<typeof equipmentSchema>;

function defaults(eq?: Equipment): FormInput {
  const byMetric = new Map(eq?.thresholds.map((t) => [t.metric, t]));
  return {
    code: eq?.code ?? "",
    name: eq?.name ?? "",
    station: eq?.station ?? "",
    equipment_type: eq?.equipment_type ?? "",
    status: eq?.status ?? "idle",
    thresholds: METRICS.map((m) => {
      const t = byMetric.get(m);
      return {
        metric: m,
        warning_low: t?.warning_low ?? "",
        warning_high: t?.warning_high ?? "",
        critical_low: t?.critical_low ?? "",
        critical_high: t?.critical_high ?? "",
      };
    }),
  };
}

export function EquipmentFormModal({ open, onClose, equipment, onSaved }: { open: boolean; onClose: () => void; equipment?: Equipment; onSaved?: (e: Equipment) => void }) {
  const editing = Boolean(equipment);
  const qc = useQueryClient();
  const toast = useToast();
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<FormInput, unknown, FormOutput>({ resolver: zodResolver(equipmentSchema), defaultValues: defaults(equipment) });

  useEffect(() => {
    if (open) reset(defaults(equipment));
  }, [open, equipment, reset]);

  const mutation = useMutation({
    mutationFn: (values: FormOutput) => {
      // Rows with no limits at all are omitted (backend requires ≥1 limit per threshold row).
      const thresholds = values.thresholds.filter((t) => [t.warning_low, t.warning_high, t.critical_low, t.critical_high].some((v) => v !== null)) as Threshold[];
      const body = { name: values.name, station: values.station, equipment_type: values.equipment_type, status: values.status, thresholds };
      return equipment ? api.updateEquipment(equipment.id, body) : api.createEquipment({ ...body, code: values.code });
    },
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["equipment"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success(editing ? `Saved ${saved.code}` : `Created ${saved.code}`);
      onSaved?.(saved);
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 409) setError("code", { message: err.message });
        for (const [field, msg] of Object.entries(err.fieldErrors())) {
          if (["code", "name", "station", "equipment_type", "status"].includes(field)) setError(field as "code", { message: msg });
        }
      }
      toast.error(editing ? "Could not save equipment" : "Could not create equipment", err);
    },
  });

  const tErr = (i: number, k: string) => (errors.thresholds?.[i] as Record<string, { message?: string }> | undefined)?.[k]?.message;

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title={editing ? `Edit ${equipment?.code}` : "Add equipment"}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="equipment-form" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving…" : editing ? "Save changes" : "Create equipment"}
          </Button>
        </>
      }
    >
      <form id="equipment-form" noValidate onSubmit={handleSubmit((v) => mutation.mutate(v))} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Equipment ID" htmlFor="eq-code" error={errors.code?.message} hint={editing ? "IDs cannot be changed" : "e.g. PRESS-04"}>
            <input id="eq-code" {...register("code")} disabled={editing} aria-invalid={Boolean(errors.code)} className={inputCls} />
          </Field>
          <Field label="Name" htmlFor="eq-name" error={errors.name?.message}>
            <input id="eq-name" {...register("name")} aria-invalid={Boolean(errors.name)} className={inputCls} />
          </Field>
          <Field label="Station" htmlFor="eq-station" error={errors.station?.message}>
            <input id="eq-station" {...register("station")} aria-invalid={Boolean(errors.station)} className={inputCls} />
          </Field>
          <Field label="Equipment type" htmlFor="eq-type" error={errors.equipment_type?.message}>
            <input id="eq-type" {...register("equipment_type")} aria-invalid={Boolean(errors.equipment_type)} className={inputCls} />
          </Field>
          <Field label="Operational status" htmlFor="eq-status" hint="Live telemetry updates this automatically unless set to maintenance">
            <select id="eq-status" {...register("status")} className={inputCls}>
              {EQUIPMENT_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <fieldset>
          <legend className="mb-1 text-sm font-semibold">Alert thresholds</legend>
          <p className="mb-2 text-xs text-ink-3">
            Operational limits that raise alerts. They must sit inside the sensor's physically plausible range; readings outside that range are rejected as bad data instead. Leave a row empty
            for no threshold rule.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-3">
                <tr>
                  <th className="py-1 pr-2 font-medium">Metric</th>
                  <th className="py-1 pr-2 font-medium">Critical low</th>
                  <th className="py-1 pr-2 font-medium">Warning low</th>
                  <th className="py-1 pr-2 font-medium">Warning high</th>
                  <th className="py-1 font-medium">Critical high</th>
                </tr>
              </thead>
              <tbody>
                {METRICS.map((m, i) => (
                  <tr key={m} className="align-top">
                    <th scope="row" className="py-1 pr-2 text-left font-normal whitespace-nowrap">
                      {METRIC_META[m].label} <span className="text-xs text-ink-3">({METRIC_META[m].unit}, {PLAUSIBLE[m][0]}–{PLAUSIBLE[m][1]})</span>
                      <input type="hidden" {...register(`thresholds.${i}.metric`)} />
                    </th>
                    {(["critical_low", "warning_low", "warning_high", "critical_high"] as const).map((k) => (
                      <td key={k} className="py-1 pr-2">
                        <input
                          inputMode="decimal"
                          aria-label={`${METRIC_META[m].label} ${k.replace("_", " ")}`}
                          aria-invalid={Boolean(tErr(i, k))}
                          {...register(`thresholds.${i}.${k}`)}
                          className={`${inputCls} w-24`}
                        />
                        {tErr(i, k) && (
                          <p role="alert" className="mt-0.5 max-w-28 text-[0.7rem] text-critical">
                            {tErr(i, k)}
                          </p>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
