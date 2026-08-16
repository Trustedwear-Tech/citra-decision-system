# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Give the three hand-authored agents their tools.

All three shipped with tools_v2: [] — I authored the agent specs and never wired
any. The symptoms looked unrelated and none of them looked like this:

  * the claims agent narrated "Action: ds_policy / Action Input: {...}" as prose
    instead of calling anything — it was improvising a tool protocol because it
    had no functions to call, and produced no decision and no planned_writes;
  * the collections agent answered "retrieve contact history first, then..."
    conditionally, because it could not read the contact history;
  * the sales Executive Brief told the reader "no data tools are wired to this
    app" — the only one of the three that said so plainly — while the charts
    beside it were full of live data.

Write-tool input schemas are lifted from sources.json rather than retyped, so
they cannot drift from what the MCP will actually accept.
"""
import json, pathlib

TENANT = pathlib.Path(r"C:/Github/Citra-AI/demo-data/tenants/acme-bank")
APPS = TENANT / "apps"
SOURCES = json.loads((TENANT / "mcp" / "sources.json").read_text(encoding="utf-8"))
SRCS = SOURCES if isinstance(SOURCES, list) else (SOURCES.get("sources") or [])

SRC_NAME = {s["source_id"]: s.get("name") for s in SRCS}
WRITE = {}
for s in SRCS:
    for ds in (s.get("datasets") or []):
        for wa in (ds.get("write_actions") or []):
            WRITE[wa["id"]] = (s["source_id"], ds["id"], wa)


COLUMNS = {}
for _s in SRCS:
    for _ds in (_s.get("datasets") or []):
        cols = _ds.get("columns") or []
        COLUMNS[_ds["id"]] = [c.get("name") if isinstance(c, dict) else str(c)
                              for c in cols]


def read_tool(name, source_id, dataset_id, description):
    # Name the queryable columns. Without them the model invents filters — the
    # claims agent queried claim_documents by customer_id and policy_no, neither
    # of which exists there, and burned two of its tool calls on SQL errors.
    cols = COLUMNS.get(dataset_id) or []
    if cols:
        description = f"{description} Columns: {', '.join(cols)}."
    return {"name": name, "description": description, "kind": "mcp",
            "source_id": source_id, "tool_name": SRC_NAME[source_id],
            "input_schema_ref": None, "dataset_id": dataset_id,
            "dataset_kind": "sql", "required": False}


def rag_tool(description):
    return {"name": "search_policy_library", "description": description,
            "kind": "rag", "source_id": "acme_bank_policy_library",
            "top_k": 8, "classification_max": None}


def write_tool(action_id, description):
    source_id, dataset_id, wa = WRITE[action_id]
    return {"name": action_id, "description": description, "kind": "mcp_action",
            "source_id": source_id, "dataset_id": dataset_id,
            "action_id": action_id, "input_schema": wa["input_schema"],
            "editable_fields": []}


PLAN = {
    "02_collections_priority.json": [
        read_tool("lookup_contact_history", "loan_servicing",
                  "loan_servicing.collection_activities",
                  "Contact attempts already made on this loan account — channel, "
                  "outcome, promise-to-pay date/amount and whether it was kept. "
                  "Read this BEFORE recommending the next contact: a broken PTP "
                  "is a different situation from a first attempt."),
        read_tool("lookup_loan_account", "loan_servicing",
                  "loan_servicing.loan_accounts",
                  "The loan account — product, sanctioned amount, EMI, tenure "
                  "and current status."),
        read_tool("lookup_repayment_schedule", "loan_servicing",
                  "loan_servicing.repayment_schedule",
                  "Instalment-level history for the account, including which "
                  "instalments bounced."),
        rag_tool("Search the Collections & Recovery SOP, the Fair Practices "
                 "Code and the NPA circular for contact rules, PTP handling, "
                 "dispute routing and calling hours."),
        write_tool("log_collection_activity",
                   "Log the contact attempt and its outcome against the loan "
                   "account, including any promise to pay."),
    ],
    "03_claim_triage.json": [
        read_tool("lookup_policy", "insurance_claims", "insurance_claims.policies",
                  "The policy behind this claim — cover, sum insured, "
                  "inception/expiry dates and exclusions."),
        read_tool("lookup_claim_documents", "insurance_claims",
                  "insurance_claims.claim_documents",
                  "Documents filed against this claim, with the content hash "
                  "used to spot an estimate reused across claims."),
        read_tool("lookup_surveyor_report", "insurance_claims",
                  "insurance_claims.surveyor_reports",
                  "The surveyor's assessed loss and remarks, where a survey "
                  "has been done."),
        rag_tool("Search the motor and health claim settlement SOPs, the claims "
                 "fraud-indicators circular and the grievance policy for "
                 "settlement rules, exclusions and intimation windows."),
        write_tool("record_claim_decision",
                   "Record the claim decision — approved amount or the "
                   "repudiation reason, with the deciding officer."),
    ],
    "04_sales_performance.json": [
        read_tool("lookup_opportunities", "sales_crm", "sales_crm.opportunities",
                  "Booked and open opportunities — product, stage, booked value "
                  "and close date. Use for what was actually sold."),
        read_tool("lookup_leads", "sales_crm", "sales_crm.leads",
                  "Leads with status, source, product interest and expected "
                  "value — including the unworked ones."),
        read_tool("lookup_branches", "sales_crm", "sales_crm.branches",
                  "Branch network — code, city, state, region and cluster head."),
        read_tool("lookup_agents", "sales_crm", "sales_crm.agents",
                  "Sales agents, their branch and channel."),
        rag_tool("Search the sales conduct and suitability policy for what may "
                 "be sold to whom and how performance is to be reported."),
    ],
}

for fname, tools in PLAN.items():
    p = APPS / fname
    d = json.loads(p.read_text(encoding="utf-8"))
    d["agent_spec"]["tools_v2"] = tools
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    kinds = {}
    for t in tools:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print(f"{fname}: {len(tools)} tools {kinds}")
