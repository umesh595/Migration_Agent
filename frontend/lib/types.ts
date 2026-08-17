// Mirrors the backend's Pydantic schemas closely enough for the UI to render.
// Source of truth is always the API response — these types describe its shape,
// they never generate or validate it client-side.

export type WorkloadType =
  | "web_service"
  | "api_service"
  | "batch_job"
  | "database"
  | "message_queue"
  | "cache"
  | "ml_inference"
  | "ml_training"
  | "data_pipeline"
  | "data_warehouse"
  | "storage"
  | "load_balancer"
  | "cdn"
  | "third_party_integration"
  | "other";

export type DependencyKind =
  | "sync_call"
  | "async_call"
  | "data_read"
  | "data_write"
  | "event_publish"
  | "event_subscribe"
  | "network_route"
  | "other";

export type Environment = "on_prem" | "cloud" | "hybrid" | "unknown";

export interface Component {
  id: string;
  name: string;
  workload_type: WorkloadType;
  environment: Environment;
  description: string;
  technology: string | null;
  owner_team: string | null;
  criticality: string | null;
}

export interface Dependency {
  id: string;
  source_id: string;
  target_id: string;
  kind: DependencyKind;
  description: string;
}

export interface Assumption {
  id: string;
  text: string;
  raised_by: string;
  related_component_ids: string[];
}

export interface OpenQuestion {
  id: string;
  text: string;
  related_component_ids: string[];
  resolved: boolean;
}

export interface ArchitectureModel {
  components: Component[];
  dependencies: Dependency[];
  assumptions: Assumption[];
  open_questions: OpenQuestion[];
  status: "draft" | "accepted";
  version: number;
}

export type SevenR = "rehost" | "replatform" | "repurchase" | "refactor" | "retain" | "retire" | "relocate";

export interface ComponentMapping {
  component_id: string;
  target_description: string;
  disposition: SevenR;
}

export interface ValidationCheck {
  description: string;
  check_type: string;
}

export interface ComponentPlan {
  component_id: string;
  disposition: SevenR;
  wave_index: number;
  steps: string[];
  validation_checks: ValidationCheck[];
  rollback_notes: string;
  estimated_effort: string | null;
  dependencies_considered: string[];
}

export interface CoexistenceGroup {
  component_ids: string[];
  reason: string;
  coexistence_strategy: string;
}

export interface Wave {
  index: number;
  component_ids: string[];
  rationale: string;
  coexistence_groups: CoexistenceGroup[];
}

export type RiskSeverity = "low" | "medium" | "high" | "critical";

export interface Risk {
  id: string;
  description: string;
  severity: RiskSeverity;
  mitigation: string;
  related_component_ids: string[];
  source: string;
}

export interface CutoverStrategy {
  approach: string;
  steps: string[];
  go_no_go_criteria: string[];
  communication_plan: string;
}

export interface RollbackStrategy {
  approach: string;
  triggers: string[];
  steps: string[];
  data_reconciliation_notes: string | null;
}

export interface ValidationSummary {
  overall_strategy: string;
  cross_component_checks: ValidationCheck[];
  sign_off_gates: string[];
}

export interface RoadmapItem {
  wave_index: number;
  component_id: string;
  disposition: SevenR;
  summary: string;
  owner_placeholder: string;
  estimated_effort: string | null;
  depends_on_waves: number[];
}

export interface MigrationPlan {
  target_architecture_description: string;
  component_mappings: ComponentMapping[];
  component_plans: ComponentPlan[];
  waves: Wave[];
  risks: Risk[];
  cutover_strategy: CutoverStrategy | null;
  rollback_strategy: RollbackStrategy | null;
  validation_summary: ValidationSummary | null;
  roadmap_items: RoadmapItem[];
  status: "draft" | "reviewed" | "final";
  version: number;
}

export type DowntimeTolerance = "zero_downtime" | "maintenance_window" | "flexible";

export interface MigrationContext {
  source_environment: Environment;
  target_environment: Environment;
  target_platform_description: string;
  downtime_tolerance: DowntimeTolerance;
  maintenance_window_description: string | null;
  constraints: string[];
  target_completion_description: string | null;
}

export type SessionStatus = "discovery" | "planning" | "review" | "exported";

export interface SessionSummary {
  id: string;
  name: string;
  status: SessionStatus;
  token_usage: number;
}

export interface SessionState {
  session: SessionSummary;
  model: ArchitectureModel;
  plan: MigrationPlan | null;
  migration_context: MigrationContext | null;
}

export type FindingSeverity = "info" | "warning" | "error";
export type FindingSource = "rule" | "llm";
export type ResolutionStatus = "open" | "resolved" | "accepted_as_risk";

export interface Finding {
  source: FindingSource;
  rule_id: string | null;
  severity: FindingSeverity;
  message: string;
  related_component_ids: string[];
  resolution_status: ResolutionStatus;
}

export interface PatchAuditEntry {
  patch: Record<string, unknown>;
  outcome: "applied" | "rejected";
  reason: string | null;
  model_version_before: number;
  model_version_after: number | null;
}

export interface TurnCompleteEvent {
  narration: string | null;
  questions: string[];
  clarifying_questions: string[];
  error: string | null;
  model_version: number | null;
  tokens_used: number;
}

export interface NodeCompleteEvent {
  node: string;
  narration: string | null;
}

export interface ReviewQualityScore {
  iteration: number;
  evaluated_finding_count: number;
  relevance_score: number;
  specificity_score: number;
  actionability_score: number;
  context_awareness_score: number;
  overall_score: number;
  rationale: string;
  flagged_issues: string[];
}

export interface AdminUser {
  id: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
}
