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
        },
        {
            "id": 11,
            "scenario": "Typo in Product Name ('satu aata' instead of Sattu Aata)",
            "query": "satu aata 500g ka rate kya tha last bill me",
            "expectation": "Correctly matches (1124) Gangwal Sattu Aata 500gm and provides latest rate (₹47.00/Pcs)."
        },
        {
            "id": 12,
            "scenario": "Phonetic Spelling / Slang ('khaman miks', 'kitne me diya')",
            "query": "khaman miks 500gm kitne me diya tha pichli bar?",
            "expectation": "Matches (0028) Gangwal Khaman Mix 500gm and returns latest rate (₹62.00/Pcs)."
        },
        {
            "id": 13,
            "scenario": "Informal Invoices Request in Hindi ('purane sales bills dikhao')",
            "query": "mere purane sales bills dikhao jo last month ke the",
            "expectation": "Understands sales invoices request and presents recent bill records."
        },
        {
            "id": 14,
            "scenario": "Typo in Item Name ('makka ata' instead of Makka Aata)",
            "query": "makka ata 1 kg ka price btao",
            "expectation": "Matches (0509) Gangwal Makka Aata 1kg and gives latest rate (₹29.00/Pcs)."
        },
        {
            "id": 15,
            "scenario": "Local Trade Terminology ('bhav' + typo 'beson')",
            "query": "beson sada 1kg ka kya bhav lagaya tha?",
            "expectation": "Understands 'bhav' as rate check, matches (0547) Gangwal Besan Sada 1kg (₹79.00/Pcs)."
        },
        {
            "id": 16,
            "scenario": "Spelling Variation ('chaval aata' instead of Chawal Aata)",
            "query": "chaval aata 500gm ka last rate check krna h",
            "expectation": "Matches (0677) Gangwal Chawal Aata 500gm (₹20.00/Pcs)."
        },
        {
            "id": 17,
            "scenario": "Ambiguous Partial Name with Typo ('bhajiya mix')",
            "query": "bhajiya mix ka price kya h",
            "expectation": "Detects ambiguity between Bhajiya Mix 500gm and Mung Bhajiya Mix 500gm, asking customer to clarify."
        },
        {
            "id": 18,
            "scenario": "Multiple Typos in Brand and Item ('gangwl poaha')",
            "query": "what was the cost of gangwl poaha 1kg in previous delivery?",
            "expectation": "Identifies (1803) Gangwal Poha 1kg and returns latest invoice rate (₹52.00/Pcs)."
        },
        {
            "id": 19,
            "scenario": "Multi-Intent Message with Slang ('mera balance kitna h aur dosa miks ka rate btao')",
            "query": "mera balance kitna h aur dosa miks ka rate btao",
            "expectation": "Answers both outstanding amount and Dosa Mix 500gm rate (₹75.00/Pcs) in a single unified message."
        },
        {
            "id": 20,
            "scenario": "Conversational Query with Invoice Number Request",
            "query": "last bill me bajra aata 1kg ka kya rate laga tha invoice no k sath",
            "expectation": "Matches (0516) Gangwal Bajra Aata 1kg, reports latest rate (₹30.00/Pcs) and exact invoice reference."
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
        "| # | Scenario | Query | Classified Intent(s) | Status | Full Agent Response |",
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

    output_path = "/Users/finbook/Documents/GitHub/agents/SALES_HISTORY_TEST.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"Successfully generated {output_path}")

if __name__ == "__main__":
    main()
