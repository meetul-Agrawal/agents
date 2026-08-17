from __future__ import annotations

import json
from datetime import datetime
from src.ca import orchestrator as orc
from src.ca import customer360 as c3


def main():
    cid = "6a6464a19f707bd30403790f"
    customer = c3.get_customer(cid)

    test_queries = [
        {
            "id": 1,
            "scenario": "Recent Payments List (English)",
            "query": "Show my last 5 payments with date and amount",
            "expectation": "Returns top 5 most recent receipt vouchers with dates and amounts."
        },
        {
            "id": 2,
            "scenario": "Total All-Time Payments (Hinglish)",
            "query": "Total kitna payment hua hai abhi tak hamari taraf se?",
            "expectation": "Returns total receipts count, total amount, and last payment date."
        },
        {
            "id": 3,
            "scenario": "Specific Period Payment Enquiry (Last 30 Days)",
            "query": "Last 30 days me kitna payment receive hua hai?",
            "expectation": "Calculates payments made in the last 30 days."
        },
        {
            "id": 4,
            "scenario": "Financial Year Enquiry (FY 25-26)",
            "query": "FY 25-26 me total kitna amount pay kiya tha?",
            "expectation": "Filters receipts within Indian FY 2025-26 and sums amount."
        },
        {
            "id": 5,
            "scenario": "Average Settlement Speed Check",
            "query": "Hamara payment settlement speed aur average days kitna hai?",
            "expectation": "Returns average days to settle and total settled bills."
        },
        {
            "id": 6,
            "scenario": "Latest Single Payment Check",
            "query": "Mera last payment kab aur kitne ka hua tha?",
            "expectation": "Identifies most recent payment date and amount."
        },
        {
            "id": 7,
            "scenario": "High Value Payment Enquiry",
            "query": "Have you received my payments of 10 lakhs or more?",
            "expectation": "Filters receipts with amount >= 10,00,000."
        },
        {
            "id": 8,
            "scenario": "Specific Receipt Voucher Reference",
            "query": "Receipt Rec/Bank/U2/19 ka details dikhao",
            "expectation": "Looks up receipt Rec/Bank/U2/19 and returns date, amount, and UTR narration."
        },
        {
            "id": 9,
            "scenario": "Multi-Intent (Outstanding Balance + Last Payment Date)",
            "query": "Mera total balance kitna bacha hai aur pichhla payment kab hua tha?",
            "expectation": "Combines outstanding balance enquiry and payment history enquiry."
        },
        {
            "id": 10,
            "scenario": "Informal Hindi 3-Month Receipts Breakdown",
            "query": "Kab kab paise bheje the humne pichhle 3 mahine me?",
            "expectation": "Understands 3-month receipts request and lists recent payments."
        }
    ]

    report_lines = [
        "# PAYMENT HISTORY END-TO-END TEST REPORT",
        "",
        f"**Test Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Customer Tested**: `{customer.display_name}` (`{cid}`)",
        "**Agent Evaluated**: `sa1_general` orchestrated through `CustomerAssistState` pipeline",
        "**Backend Data**: Live MongoDB vouchers (`tenant_db`)",
        "",
        "---",
        "",
        "## Test Summary Table",
        "",
        "| # | Scenario | Query | Classified Intent(s) | Status | Full Agent Response |",
        "|---|---|---|---|---|---|"
    ]

    detailed_sections = []

    for item in test_queries:
        q_id = item["id"]
        q_text = item["query"]
        q_scen = item["scenario"]
        q_exp = item["expectation"]

        print(f"Executing Payment Test #{q_id}: {q_text}", flush=True)
        state = orc.handle(q_text, customer_id=cid)
        summary = orc.summarize(state)

        intents_str = ", ".join(f"`{i}`" for i in summary["intents"])
        agents_str = ", ".join(f"`{a}`" for a in summary["agents"])
        statuses_str = ", ".join(summary["statuses"])
        full_response = (state.final_response or "").replace("\n", "<br>").replace("|", "\\|").strip()

        status_badge = "✅ Pass" if summary["statuses"] in (["completed"], ["needs_information"]) else "❌ Fail"
        report_lines.append(f"| {q_id} | {q_scen} | \"{q_text}\" | {intents_str} | {status_badge} | {full_response} |")

        sec = [
            f"### Test {q_id}: {q_scen}",
            "",
            f"- **Inbound User Query**: `\"{q_text}\"`",
            f"- **Expected Behavior**: {q_exp}",
            f"- **Classified Intent(s)**: {intents_str}",
            f"- **Routed Agent(s)**: {agents_str}",
            f"- **Execution Status**: `{statuses_str}`",
            "",
            "#### Tool Calls Executed:",
            "```json"
        ]

        tool_calls_data = []
        for result in state.agent_results:
            for call in result.tool_calls:
                tool_calls_data.append({
                    "tool": call.tool,
                    "arguments": call.arguments,
                    "ok": call.ok,
                })
        sec.append(json.dumps(tool_calls_data, indent=2, default=str))
        sec.append("```")
        sec.append("")
        sec.append("#### End-to-End Agent Response:")
        sec.append("> " + (state.final_response or "").replace("\n", "\n> "))
        sec.append("")
        sec.append("---")
        detailed_sections.append("\n".join(sec))

    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append("## Detailed End-to-End Test Execution Traces")
    report_lines.append("")
    report_lines.extend(detailed_sections)

    output_path = "/Users/finbook/Documents/GitHub/agents/PAYMENT_HISTORY_TEST.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"Successfully generated {output_path}", flush=True)

if __name__ == "__main__":
    main()
