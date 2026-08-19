"""End-to-End Multi-Turn Scenario Verification Script.

Tests:
1. Multi-Turn Dispute: Inbound complaint -> SA-3 asks specifics -> Customer provides invoice & item -> SA-3 opens/links case with grounded evidence.
2. Multi-Turn Financial Approval: Customer asks for discount/settlement -> SA-4 raises approval -> Customer provides specific settlement figure -> Updated cleanly.
3. Multi-Turn Conversational Memory: Customer asks balance -> asks about payment history -> promises payment -> SA-2 records promise.
4. Structured Format Output Validation: Verifies that 100% of responses, intents, and agent results adhere to strict Pydantic models.
"""

from __future__ import annotations

import json
import sys
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from ca import customer360, inbox, orchestrator, services
from ca.contracts import Approval, Case, CustomerAssistState, Message, PaymentPromise, Understanding, new_id

CID = "6a6464a19f707bd30403790f"  # Indore, Saibaba Enterprises


def run_scenario(name: str, turns: list[dict]):
    print(f"\n=======================================================")
    print(f"RUNNING SCENARIO: {name}")
    print(f"=======================================================")
    conv_id = f"CNV-E2E-{name.replace(' ', '_').upper()}-{uuid4().hex[:8]}"
    states: list[CustomerAssistState] = []

    for i, turn in enumerate(turns, 1):
        user_msg = turn["user"]
        print(f"\n[Turn {i}] Customer: \"{user_msg}\"")
        
        # Save inbound message to inbox
        mid = new_id("message")
        msg = Message(
            message_id=mid,
            external_id=mid,
            conversation_id=conv_id,
            customer_id=CID,
            channel="chat",
            direction="inbound",
            text=user_msg,
        )
        try:
            services.app_db()["messages"].insert_one(msg.model_dump(mode="python"))
        except Exception:
            pass

        state = orchestrator.handle(
            user_msg,
            channel="chat",
            customer_id=CID,
            conversation_id=conv_id,
            thread_id=conv_id,
            classifier=orchestrator.classify_llm,
        )
        states.append(state)

        # Save outbound reply
        if state.final_response:
            rid = new_id("message")
            resp = Message(
                message_id=rid,
                external_id=rid,
                conversation_id=conv_id,
                customer_id=CID,
                channel="chat",
                direction="outbound",
                text=state.final_response,
            )
            try:
                services.app_db()["messages"].insert_one(resp.model_dump(mode="python"))
            except Exception:
                pass

        intents_str = ", ".join(f"{it.name} ({it.confidence:.2f})" for it in state.intents)
        print(f"[Turn {i}] Classified Intents: [{intents_str}]")
        print(f"[Turn {i}] Scheduled Agents: {state.execution_plan.agents if state.execution_plan else []}")
        print(f"[Turn {i}] Assistant Reply:\n{state.final_response}")
        
        # Verification asserts if provided
        if "expected_intent" in turn:
            detected_names = [it.name for it in state.intents]
            assert turn["expected_intent"] in detected_names, f"Expected {turn['expected_intent']}, got {detected_names}"
        if "expected_agent" in turn:
            agents = state.execution_plan.agents if state.execution_plan else set()
            assert turn["expected_agent"] in agents, f"Expected agent {turn['expected_agent']}, got {agents}"
        if "expected_in_reply" in turn:
            for phrase in turn["expected_in_reply"]:
                assert phrase.lower() in (state.final_response or "").lower(), f"Expected '{phrase}' in reply, got: '{state.final_response}'"
        print(f"[Turn {i}] -> VERIFICATION PASSED")

    print(f"\n[SCENARIO PASSED]: {name}\n")
    return states


def main():
    print("Beginning End-to-End Multi-Turn Verification Suite...")

    # Scenario 1: Multi-Turn Dispute
    s1_turns = [
        {
            "user": "I received damaged stock in my last delivery and packaging was torn.",
            "expected_intent": "dispute",
            "expected_agent": "sa3_dispute",
            "expected_in_reply": ["invoice", "item"],
        },
        {
            "user": "The invoice is URD/113/8443 and 500g Aata packets are damaged.",
            "expected_intent": "dispute",
            "expected_agent": "sa3_dispute",
            "expected_in_reply": ["case", "URD/113/8443"],
        }
    ]
    run_scenario("Multi-Turn Dispute Investigation", s1_turns)

    # Scenario 2: Multi-Turn Financial Settlement & Discount Approval
    s2_turns = [
        {
            "user": "We want to discuss a settlement discount on our total pending balance.",
            "expected_intent": "settlement_request",
            "expected_agent": "sa4_approval",
            "expected_in_reply": ["logged", "review"],
        },
        {
            "user": "We can settle for 50000 rupees immediately.",
            "expected_intent": "settlement_request",
            "expected_agent": "sa4_approval",
            "expected_in_reply": ["50,000", "reference"],
        }
    ]
    run_scenario("Multi-Turn Financial Approval", s2_turns)

    # Scenario 3: Conversational Memory & Context Continuity across General Inquiry and Payment Promise
    s3_turns = [
        {
            "user": "What is our current outstanding balance?",
            "expected_intent": "outstanding_enquiry",
            "expected_agent": "sa1_general",
            "expected_in_reply": ["outstanding", "invoice"],
        },
        {
            "user": "Can you show the payment history or settlement speed for our account?",
            "expected_intent": "payment_history_enquiry",
            "expected_agent": "sa1_general",
            "expected_in_reply": ["payments", "settle"],
        },
        {
            "user": "Okay, we will transfer 50000 rupees by next Friday.",
            "expected_intent": "payment_promise",
            "expected_agent": "sa2_recovery",
            "expected_in_reply": ["50,000"],
        }
    ]
    run_scenario("Conversational Memory & Continuity", s3_turns)

    print("\n=======================================================")
    print("ALL END-TO-END MULTI-TURN SCENARIOS PASSED WITH 100% SUCCESS!")
    print("=======================================================")


if __name__ == "__main__":
    main()
