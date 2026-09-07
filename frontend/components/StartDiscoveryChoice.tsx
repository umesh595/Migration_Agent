"use client";

import { useId, useRef, useState } from "react";

import { ApiError, connectAws, importDocument } from "@/lib/api";
import type {
  AwsConnectResult,
  DependencyKind,
  DocumentImportResult,
  Environment,
  WorkloadType,
} from "@/lib/types";

// Framed entirely around what the user HAS to start from (a mental model to
// type out, a document, a live cloud account) — never around who they are.
// Silently inferring technical vs. non-technical from message content
// (see backend's user_technical_level) is the right way to adapt question
// depth; asking the user to self-classify up front is not, and this screen
// deliberately never asks it.

const WORKLOAD_TYPES: WorkloadType[] = [
  "web_service",
  "api_service",
  "batch_job",
  "database",
  "message_queue",
  "cache",
  "ml_inference",
  "ml_training",
  "data_pipeline",
  "data_warehouse",
  "storage",
  "load_balancer",
  "cdn",
  "third_party_integration",
  "other",
];

const ENVIRONMENTS: Environment[] = ["on_prem", "cloud", "hybrid", "unknown"];

const DEPENDENCY_KINDS: DependencyKind[] = [
  "sync_call",
  "async_call",
  "data_read",
  "data_write",
  "event_publish",
  "event_subscribe",
  "network_route",
  "other",
];

function humanize(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

interface FormComponentRow {
  key: string;
  name: string;
  workloadType: WorkloadType;
  environment: Environment;
  technology: string;
  criticality: string;
}

interface FormDependencyRow {
  key: string;
  sourceKey: string;
  targetKey: string;
  kind: DependencyKind;
  description: string;
}

function newComponentRow(key: string): FormComponentRow {
  return { key, name: "", workloadType: "api_service", environment: "unknown", technology: "", criticality: "" };
}

function newDependencyRow(key: string, sourceKey = "", targetKey = ""): FormDependencyRow {
  return { key, sourceKey, targetKey, kind: "sync_call", description: "" };
}

function serializeQuestionnaire(components: FormComponentRow[], dependencies: FormDependencyRow[]): string {
  const named = components.filter((c) => c.name.trim());
  const byKey = new Map(named.map((c) => [c.key, c]));

  const lines: string[] = ["Here is my system architecture, filled in as a structured form:", "", "Components:"];
  for (const c of named) {
    const details = [`${humanize(c.workloadType)}`, `environment: ${humanize(c.environment)}`];
    if (c.technology.trim()) details.push(`technology: ${c.technology.trim()}`);
    if (c.criticality.trim()) details.push(`criticality: ${c.criticality.trim()}`);
    lines.push(`- ${c.name.trim()} (${details.join("; ")})`);
  }

  const namedDeps = dependencies.filter((d) => byKey.has(d.sourceKey) && byKey.has(d.targetKey));
  if (namedDeps.length) {
    lines.push("", "Dependencies:");
    for (const d of namedDeps) {
      const source = byKey.get(d.sourceKey)!.name.trim();
      const target = byKey.get(d.targetKey)!.name.trim();
      const desc = d.description.trim() ? ` — ${d.description.trim()}` : "";
      lines.push(`- ${source} -> ${target} (${humanize(d.kind)})${desc}`);
    }
  }

  return lines.join("\n");
}

function OptionCard({
  icon,
  title,
  description,
  onClick,
}: {
  icon: string;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="card flex flex-col items-start gap-1.5 !p-4 text-left transition hover:border-brand-400/40 hover:bg-white/[0.06]"
    >
      <span className="text-xl">{icon}</span>
      <span className="text-sm font-semibold text-slate-100">{title}</span>
      <span className="text-xs leading-5 text-slate-400">{description}</span>
    </button>
  );
}

export function StartDiscoveryChoice({
  sessionId,
  placeholder,
  onSendText,
  onDocumentImported,
  onAwsConnected,
  onDismiss,
}: {
  sessionId: string;
  placeholder: string;
  onSendText: (text: string) => Promise<void>;
  onDocumentImported: (result: DocumentImportResult) => void;
  onAwsConnected: (result: AwsConnectResult) => void;
  onDismiss: () => void;
}) {
  const [mode, setMode] = useState<"choices" | "form" | "document" | "aws">("choices");
  const formIdSeed = useId();
  const keyCounter = useRef(0);

  function makeKey() {
    keyCounter.current += 1;
    return `${formIdSeed}-${keyCounter.current}`;
  }

  // --- Structured form state ---
  // The first row's key is a fixed literal, not makeKey() — reading a ref
  // during a useState lazy initializer runs during render, which the
  // component-boundary rules flag; makeKey() is only ever called from event
  // handlers (add component / add dependency) below, which is safe.
  const [components, setComponents] = useState<FormComponentRow[]>(() => [newComponentRow(`${formIdSeed}-0`)]);
  const [dependencies, setDependencies] = useState<FormDependencyRow[]>([]);
  const [submittingForm, setSubmittingForm] = useState(false);

  // --- Document upload state ---
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const [documentError, setDocumentError] = useState<string | null>(null);

  // --- AWS connect state ---
  const [awsAccessKeyId, setAwsAccessKeyId] = useState("");
  const [awsSecretAccessKey, setAwsSecretAccessKey] = useState("");
  const [awsSessionToken, setAwsSessionToken] = useState("");
  const [awsRegion, setAwsRegion] = useState("us-east-1");
  const [connectingAws, setConnectingAws] = useState(false);
  const [awsError, setAwsError] = useState<string | null>(null);

  function updateComponent(key: string, patch: Partial<FormComponentRow>) {
    setComponents((prev) => prev.map((c) => (c.key === key ? { ...c, ...patch } : c)));
  }

  function removeComponent(key: string) {
    setComponents((prev) => prev.filter((c) => c.key !== key));
    setDependencies((prev) => prev.filter((d) => d.sourceKey !== key && d.targetKey !== key));
  }

  function updateDependency(key: string, patch: Partial<FormDependencyRow>) {
    setDependencies((prev) => prev.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  }

  const namedComponents = components.filter((c) => c.name.trim());
  const canSubmitForm = namedComponents.length > 0 && !submittingForm;

  async function handleFormSubmit() {
    if (!canSubmitForm) return;
    setSubmittingForm(true);
    try {
      await onSendText(serializeQuestionnaire(components, dependencies));
    } finally {
      setSubmittingForm(false);
    }
  }

  async function handleDocumentSubmit() {
    if (!selectedFile || uploadingDocument) return;
    setUploadingDocument(true);
    setDocumentError(null);
    try {
      const result = await importDocument(sessionId, selectedFile);
      onDocumentImported(result);
    } catch (err) {
      setDocumentError(err instanceof ApiError ? err.detail : "Could not import that document.");
    } finally {
      setUploadingDocument(false);
    }
  }

  async function handleAwsSubmit() {
    if (!awsAccessKeyId.trim() || !awsSecretAccessKey.trim() || connectingAws) return;
    setConnectingAws(true);
    setAwsError(null);
    try {
      const result = await connectAws(sessionId, {
        access_key_id: awsAccessKeyId.trim(),
        secret_access_key: awsSecretAccessKey.trim(),
        session_token: awsSessionToken.trim() || undefined,
        region: awsRegion.trim() || "us-east-1",
      });
      onAwsConnected(result);
      setAwsAccessKeyId("");
      setAwsSecretAccessKey("");
      setAwsSessionToken("");
      setMode("choices");
    } catch (err) {
      setAwsError(err instanceof ApiError ? err.detail : "Could not connect to AWS with those credentials.");
    } finally {
      setConnectingAws(false);
    }
  }

  if (mode === "choices") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 px-2 py-6">
        <p className="max-w-sm text-center text-sm text-slate-500">{placeholder}</p>
        <div className="grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-3">
          <OptionCard
            icon="📝"
            title="Fill in a form"
            description="Already know the shape of your system? Add each component and how they connect."
            onClick={() => setMode("form")}
          />
          <OptionCard
            icon="📄"
            title="Upload a document"
            description="Have an architecture doc, design memo, or diagram export? Upload it as PDF, Word, or text."
            onClick={() => setMode("document")}
          />
          <OptionCard
            icon="💬"
            title="Just describe it"
            description="Type it in your own words in the box below — I'll ask about anything I still need to know."
            onClick={onDismiss}
          />
        </div>
        <button
          type="button"
          onClick={() => setMode("aws")}
          className="mt-1 flex items-center gap-1.5 text-xs font-medium text-brand-200 underline-offset-2 hover:text-brand-100 hover:underline"
        >
          🔗 Optional: connect a live AWS account so I can cross-reference real infrastructure as we go
        </button>
      </div>
    );
  }

  if (mode === "document") {
    return (
      <div className="mx-auto flex h-full w-full max-w-md flex-col justify-center gap-3 py-6">
        <button type="button" onClick={() => setMode("choices")} className="self-start text-xs text-slate-500 hover:text-slate-300">
          ← Back
        </button>
        <h4 className="text-sm font-semibold text-slate-100">Upload an architecture document</h4>
        <p className="text-xs leading-5 text-slate-400">
          PDF, DOCX, or plain text. I'll read it exactly the way I'd read a typed description — same questions apply
          afterward.
        </p>
        <label className="card flex cursor-pointer flex-col items-center gap-2 !p-6 text-center text-xs text-slate-400 hover:border-brand-400/40">
          <input
            type="file"
            accept=".pdf,.docx,.doc,.txt,.md,application/pdf,text/plain"
            className="sr-only"
            onChange={(e) => {
              setDocumentError(null);
              setSelectedFile(e.target.files?.[0] ?? null);
            }}
          />
          <span className="text-2xl">📄</span>
          {selectedFile ? (
            <span className="font-medium text-slate-200">{selectedFile.name}</span>
          ) : (
            <span>Click to choose a file</span>
          )}
        </label>
        {documentError && (
          <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">
            {documentError}
          </p>
        )}
        <button type="button" className="btn-primary" disabled={!selectedFile || uploadingDocument} onClick={handleDocumentSubmit}>
          {uploadingDocument ? "Reading document…" : "Import document"}
        </button>
      </div>
    );
  }

  if (mode === "aws") {
    return (
      <div className="mx-auto flex h-full w-full max-w-md flex-col justify-center gap-3 py-6">
        <button type="button" onClick={() => setMode("choices")} className="self-start text-xs text-slate-500 hover:text-slate-300">
          ← Back
        </button>
        <h4 className="text-sm font-semibold text-slate-100">Connect your AWS account</h4>
        <p className="text-xs leading-5 text-slate-400">
          Read-only. Used once to scan EC2, RDS, Lambda, and S3, then kept in memory only for the rest of this
          session so I can cross-reference live infrastructure instead of asking questions the scan can already
          answer — never written to a database, never logged.
        </p>
        <div className="space-y-2">
          <input
            className="input"
            placeholder="Access key ID"
            value={awsAccessKeyId}
            onChange={(e) => setAwsAccessKeyId(e.target.value)}
            autoComplete="off"
          />
          <input
            className="input"
            type="password"
            placeholder="Secret access key"
            value={awsSecretAccessKey}
            onChange={(e) => setAwsSecretAccessKey(e.target.value)}
            autoComplete="off"
          />
          <input
            className="input"
            placeholder="Session token (optional)"
            value={awsSessionToken}
            onChange={(e) => setAwsSessionToken(e.target.value)}
            autoComplete="off"
          />
          <input
            className="input"
            placeholder="Region"
            value={awsRegion}
            onChange={(e) => setAwsRegion(e.target.value)}
          />
        </div>
        {awsError && (
          <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">
            {awsError}
          </p>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={!awsAccessKeyId.trim() || !awsSecretAccessKey.trim() || connectingAws}
          onClick={handleAwsSubmit}
        >
          {connectingAws ? "Connecting…" : "Connect AWS"}
        </button>
      </div>
    );
  }

  // mode === "form"
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-3 overflow-y-auto py-4">
      <button type="button" onClick={() => setMode("choices")} className="self-start text-xs text-slate-500 hover:text-slate-300">
        ← Back
      </button>
      <h4 className="text-sm font-semibold text-slate-100">Describe your architecture as a form</h4>
      <p className="text-xs leading-5 text-slate-400">
        Add each component you have, then how they connect. Submitting reads this into the same conversation as if
        you'd typed it — I'll still ask about anything important that's missing.
      </p>

      <div className="space-y-3">
        {components.map((c, i) => (
          <div key={c.key} className="card space-y-3 !p-4">
            <div className="flex items-center gap-2">
              <input
                className="input flex-1 font-medium"
                placeholder={`Component ${i + 1} name, e.g. "Orders Service"`}
                value={c.name}
                onChange={(e) => updateComponent(c.key, { name: e.target.value })}
              />
              <button
                type="button"
                className="btn-secondary !px-2.5 !py-2"
                onClick={() => removeComponent(c.key)}
                disabled={components.length === 1}
                title="Remove this component"
              >
                ✕
              </button>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <label className="label !mb-1 !text-[11px]">Type</label>
                <select
                  className="input text-sm"
                  value={c.workloadType}
                  onChange={(e) => updateComponent(c.key, { workloadType: e.target.value as WorkloadType })}
                >
                  {WORKLOAD_TYPES.map((w) => (
                    <option key={w} value={w}>
                      {humanize(w)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label !mb-1 !text-[11px]">Environment</label>
                <select
                  className="input text-sm"
                  value={c.environment}
                  onChange={(e) => updateComponent(c.key, { environment: e.target.value as Environment })}
                >
                  {ENVIRONMENTS.map((env) => (
                    <option key={env} value={env}>
                      {humanize(env)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label !mb-1 !text-[11px]">Technology (optional)</label>
                <input
                  className="input text-sm"
                  placeholder="e.g. Node.js on ECS"
                  value={c.technology}
                  onChange={(e) => updateComponent(c.key, { technology: e.target.value })}
                />
              </div>
              <div>
                <label className="label !mb-1 !text-[11px]">Criticality (optional)</label>
                <input
                  className="input text-sm"
                  placeholder="e.g. tier-1"
                  value={c.criticality}
                  onChange={(e) => updateComponent(c.key, { criticality: e.target.value })}
                />
              </div>
            </div>
          </div>
        ))}
        <button type="button" className="btn-secondary text-xs" onClick={() => setComponents((prev) => [...prev, newComponentRow(makeKey())])}>
          + Add component
        </button>
      </div>

      {namedComponents.length >= 2 && (
        <div className="space-y-3">
          <p className="label !mb-0">How do they connect? (optional)</p>
          {dependencies.map((d) => (
            <div key={d.key} className="card space-y-3 !p-4">
              <div className="flex items-center gap-2">
                <select
                  className="input flex-1 text-sm"
                  value={d.sourceKey}
                  onChange={(e) => updateDependency(d.key, { sourceKey: e.target.value })}
                >
                  <option value="">From…</option>
                  {namedComponents.map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <span className="shrink-0 text-slate-500">→</span>
                <select
                  className="input flex-1 text-sm"
                  value={d.targetKey}
                  onChange={(e) => updateDependency(d.key, { targetKey: e.target.value })}
                >
                  <option value="">To…</option>
                  {namedComponents.map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn-secondary shrink-0 !px-2.5 !py-2"
                  onClick={() => setDependencies((prev) => prev.filter((row) => row.key !== d.key))}
                  title="Remove this connection"
                >
                  ✕
                </button>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="label !mb-1 !text-[11px]">Kind</label>
                  <select
                    className="input text-sm"
                    value={d.kind}
                    onChange={(e) => updateDependency(d.key, { kind: e.target.value as DependencyKind })}
                  >
                    {DEPENDENCY_KINDS.map((k) => (
                      <option key={k} value={k}>
                        {humanize(k)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="label !mb-1 !text-[11px]">Note (optional)</label>
                  <input
                    className="input text-sm"
                    placeholder="e.g. writes order records"
                    value={d.description}
                    onChange={(e) => updateDependency(d.key, { description: e.target.value })}
                  />
                </div>
              </div>
            </div>
          ))}
          <button
            type="button"
            className="btn-secondary text-xs"
            onClick={() => setDependencies((prev) => [...prev, newDependencyRow(makeKey())])}
          >
            + Add connection
          </button>
        </div>
      )}

      <button type="button" className="btn-primary self-start" disabled={!canSubmitForm} onClick={handleFormSubmit}>
        {submittingForm ? "Submitting…" : "Submit architecture"}
      </button>
    </div>
  );
}
