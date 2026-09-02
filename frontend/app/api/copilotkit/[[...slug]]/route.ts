import { CopilotRuntime, InMemoryAgentRunner, createCopilotEndpoint } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";
import { handle } from "hono/vercel";

// The Node "Runtime" relay CopilotKit's React hooks (useHumanInTheLoop,
// useFrontendTool) need — see components/CopilotProvider.tsx's comment. The
// FastAPI backend (POST /sessions/ag-ui) already speaks pure AG-UI, so the
// agent here is a plain protocol-generic HttpAgent, not anything
// LangGraph-specific (LangGraphHttpAgent from @copilotkit/runtime/langgraph
// is v1-deprecated per its own type declaration, and unnecessary here since
// our backend, not this relay, owns the LangGraph graph).
const AGENT_URL = process.env.NEXT_PUBLIC_API_BASE_URL
  ? `${process.env.NEXT_PUBLIC_API_BASE_URL}/sessions/ag-ui`
  : "http://localhost:8000/sessions/ag-ui";

// HttpAgentConfig.headers is a static object, not a per-call function — so a
// module-level singleton agent can't carry a signed-in user's Bearer token
// (which differs per request). Building the runtime/agent/app fresh per
// request is the only correct way to forward the incoming Authorization
// header on to FastAPI, matching the exact reason the original hand-rolled
// SSE client (lib/api.ts's streamMessage) avoided `EventSource` in the first
// place: it can't send custom headers either.
function buildApp(authHeader: string | null) {
  const discoveryAgent = new HttpAgent({
    url: AGENT_URL,
    headers: authHeader ? { Authorization: authHeader } : {},
  });
  const runtime = new CopilotRuntime({
    agents: { default: discoveryAgent },
    runner: new InMemoryAgentRunner(),
  });
  return createCopilotEndpoint({ runtime, basePath: "/api/copilotkit" });
}

async function forward(req: Request): Promise<Response> {
  const app = buildApp(req.headers.get("authorization"));
  return handle(app)(req);
}

export const GET = forward;
export const POST = forward;
export const OPTIONS = forward;
