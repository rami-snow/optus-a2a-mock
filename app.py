"""
Optus Contracts & CRM Agent - mock A2A server for the Optus AI Hackathon lab.

Deliberately simple:
  * a2a-sdk v0.3.x (ServiceNow Australia supports A2A v0.3)
  * no LLM: a deterministic match against a small hard-coded dataset
  * agent card is PUBLIC; the execution call (POST) needs the x-api-key header

Environment variables (set these in Render):
  API_KEY     the key students/ServiceNow send in the x-api-key header
  PUBLIC_URL  the public base URL of this service, e.g. https://optus-contracts-agent.onrender.com
              (must NOT be localhost, or discovery works but execution fails)
"""
import os
import re
from difflib import SequenceMatcher

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

API_KEY = os.environ.get("API_KEY", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------- data ----
# Mirrors the lab's staging tables. Decoy ABNs are placeholders: align them
# with whatever you seeded in the ServiceNow staging tables.
CONTRACTS = {
    "CTR-44210": {
        "entity": "Meridian Logistics Group",
        "abn": "51 824 753 556",
        "sla_clause": "P1 >4hr = credit",
    },
}
CRM_ACCOUNTS = [
    {"id": "ACC-77021", "name": "Meridian Logistics Group Pty Ltd", "abn": "51 824 753 556"},
    {"id": "ACC-77022", "name": "Meridian Health Group", "abn": "11 111 111 111"},       # decoy
    {"id": "ACC-77023", "name": "Meridian Logistic Solutions", "abn": "22 222 222 222"},  # decoy
]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def reconcile(text: str) -> str:
    m = re.search(r"CTR-\d+", text, re.I)
    if not m or m.group(0).upper() not in CONTRACTS:
        return "I couldn't find a known contract ID in the request. Please include one, e.g. CTR-44210."
    cid = m.group(0).upper()
    c = CONTRACTS[cid]

    ranked = sorted(CRM_ACCOUNTS, key=lambda a: similarity(c["entity"], a["name"]), reverse=True)
    best = ranked[0]
    abn_match = best["abn"] == c["abn"]
    score = similarity(c["entity"], best["name"])

    if abn_match and score >= 0.8:
        confidence, verdict = "HIGH", "SAME customer"
    elif score >= 0.8:
        confidence, verdict = "LOW", "POSSIBLE match but the ABN differs - do not treat as the same customer without review"
    else:
        confidence, verdict = "LOW", "No confident match"

    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hr|hrs|hour|hours)\b", text, re.I)
    if verdict == "SAME customer" and hours:
        h = float(hours.group(1))
        sla = (f"The outage ({h:g}h) exceeds the 4h P1 threshold, so the SLA credit clause "
               f"('{c['sla_clause']}') appears to apply." if h > 4 else
               f"The outage ({h:g}h) is within the 4h P1 threshold, so the SLA credit clause "
               f"('{c['sla_clause']}') does not appear to apply.")
    elif verdict == "SAME customer":
        sla = f"SLA clause on contract: '{c['sla_clause']}'. No outage duration was provided."
    else:
        sla = "SLA applicability not assessed because the customer match is not confident."

    return (
        f"Contract {cid} ({c['entity']}, ABN {c['abn']}) vs CRM account {best['id']} "
        f"({best['name']}, ABN {best['abn']}): {verdict}. Confidence: {confidence} "
        f"(name similarity {score:.2f}, ABN {'matches' if abn_match else 'differs'}). {sla} "
        "This is a recommendation only - no credit has been applied or committed."
    )


# ------------------------------------------------------------- executor ----
class ContractsCRMExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(new_agent_text_message(reconcile(context.get_user_input())))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


# ----------------------------------------------------------- agent card ----
skill = AgentSkill(
    id="reconcile_contract_crm",
    name="Reconcile contract with CRM account",
    description="Given a contract ID (and optionally an outage duration), decides whether the contract "
                "and a CRM account are the same customer and whether the SLA credit clause applies.",
    tags=["contract", "sla", "crm"],
    examples=["Reconcile contract CTR-44210 against CRM for a 6 hour P1 outage."],
)

card = AgentCard(
    name="Optus Contracts & CRM Agent",
    description="Reconciles Optus contract records with CRM accounts and checks SLA credit clauses.",
    url=PUBLIC_URL + "/",
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[skill],
)

# ------------------------------------------------------------------ app ----
class ApiKeyOnExecution(BaseHTTPMiddleware):
    """Agent card (GET) stays public. Task execution (POST) needs x-api-key."""
    async def dispatch(self, request, call_next):
        if request.method == "POST" and request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"error": "invalid or missing x-api-key"}, status_code=401)
        return await call_next(request)


handler = DefaultRequestHandler(agent_executor=ContractsCRMExecutor(), task_store=InMemoryTaskStore())
app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
app.add_middleware(ApiKeyOnExecution)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
