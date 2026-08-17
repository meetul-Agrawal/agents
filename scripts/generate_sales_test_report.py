import json
from datetime import datetime
from src.ca import orchestrator as orc
from src.ca import customer360 as c3

def main():
    cid = "6a6464a19f707bd30403790f" # Indore, Saibaba Enterprises
    customer = c3.get_customer(cid)

    test_queries = [
        {
            "id": 1,
            "scenario": "Latest Single Product Price Check (English)",
            "query": "What was the latest price of Sattu Aata 500gm in my bills?",
            "expectation": "Extracts Sattu Aata, finds latest invoice rate (₹47.00/Pcs) and invoice number."
        },
        {
            "id": 2,
            "scenario": "Latest Product Rate Check (Hinglish)",
            "query": "Khaman mix 500gm ka last rate kya laga tha?",
            "expectation": "Extracts Khaman Mix, finds latest rate (₹62.00/Pcs) from invoice."
        },
        {
            "id": 3,
            "scenario": "Recent Purchase History & Invoice List",
            "query": "Show my recent purchase history and invoices",
            "expectation": "Returns top 5 most recent sales invoices with dates and amounts."
        },
        {
            "id": 4,
            "scenario": "Specific Pack Size Price Check",
            "query": "What is the rate of Wheat Aata 10kg?",
            "expectation": "Extracts Wheat Aata 10kg, retrieves latest rate (₹280.00/Pcs)."
        },
        {
            "id": 5,
            "scenario": "Product Rate Check in Hindi / Hinglish",
            "query": "Besan 1kg ka pichhla rate batao",
            "expectation": "Extracts Besan 1kg, retrieves latest recorded price and invoice."
        },
        {
            "id": 6,
            "scenario": "Item Rate Check (Poha)",
            "query": "Gangwal Poha 1kg ka last rate kya tha?",
            "expectation": "Extracts Gangwal Poha 1kg, retrieves rate (₹52.00/Pcs)."
        },
        {
            "id": 7,
            "scenario": "Ambiguous Product Name (Multiple Pack Sizes / Flavors)",
            "query": "What is the price of Aata?",
            "expectation": "Detects multiple matching products (Makka, Bajra, Sattu, Wheat, etc.) and asks customer to clarify."
        },
        {
            "id": 8,
            "scenario": "Product Not In Order History",
            "query": "What was the price of Basmati Rice 5kg in my last order?",
            "expectation": "Recognizes product pack is not in recent order history and prompts for confirmation."
        },
        {
            "id": 9,
            "scenario": "Multi-Intent Message (Outstanding Balance + Product Rate)",
            "query": "Tell me my outstanding balance and also the last rate of Dosa Mix 500gm",
            "expectation": "Classifies both outstanding_enquiry and sales_history_enquiry, combining both answers."
        },
        {
            "id": 10,
            "scenario": "Specific Item Rate Check (Idli Mix)",
            "query": "What rate was charged for Idli Mix 500gm on my last bill?",
            "expectation": "Extracts Idli Mix 500gm, returns latest price (₹60.00/Pcs)."
        }
    ]

    report_lines = [
        "# SALES HISTORY END-TO-END TEST REPORT",
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
        "| # | Scenario | Query | Classified Intent(s) | Status | Result Summary |",
        "|---|---|---|---|---|---|"
    ]

    detailed_sections = []

    for item in test_queries:
        q_id = item["id"]
        q_text = item["query"]
        q_scen = item["scenario"]
        q_exp = item["expectation"]

        print(f"Executing Test #{q_id}: {q_text}")
        state = orc.handle(q_text, customer_id=cid)
        summary = orc.summarize(state)

        intents_str = ", ".join(f"`{i}`" for i in summary["intents"])
        agents_str = ", ".join(f"`{a}`" for a in summary["agents"])
        statuses_str = ", ".join(summary["statuses"])
        response_preview = (state.final_response or "").replace("\n", " ").strip()
        if len(response_preview) > 60:
            response_preview = response_preview[:57] + "..."

        status_badge = "✅ Pass" if summary["statuses"] in (["completed"], ["needs_information"]) else "❌ Fail"
        report_lines.append(f"| {q_id} | {q_scen} | \"{q_text}\" | {intents_str} | {status_badge} | {response_preview} |")

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

    output_path = "/Users/finbook/Documents/GitHub/agents/SALES_HISTORY_TEST.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"Successfully generated {output_path}")

if __name__ == "__main__":
    main()
