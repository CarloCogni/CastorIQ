import os
import sys
import json
import anthropic

# 1. Setup paths correctly
SKILLS_BASE_PATH = r"D:\Resources\01 Zigurate\02 AI\Claude skills"

if not os.path.exists(SKILLS_BASE_PATH):
    print(f"ERROR: Skills path not found at {SKILLS_BASE_PATH}")
    sys.exit(1)

if SKILLS_BASE_PATH not in sys.path:
    sys.path.append(SKILLS_BASE_PATH)

# Ensure the API key is set
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY environment variable is missing!")
    print("Set it with:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)

# 2. Tool definitions
tools = [
    {
        "name": "context_engineering_skill",
        "description": (
            "Optimizes and filters large datasets or BIM/IFC project context. "
            "Use this FIRST to extract and compress relevant hospital project status "
            "data — milestones, stage gates, compliance scores, open issues — "
            "removing noise so only decision-critical information remains."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_context": {
                    "type": "string",
                    "description": "The raw project status data to filter and compress."
                }
            },
            "required": ["raw_context"]
        }
    },
    {
        "name": "ui_ux_pro_max_skill",
        "description": (
            "Converts filtered project data into a professional visual report using "
            "UI/UX Pro Max design principles (67 UI styles, 161 color palettes, "
            "25 chart types). Use this AFTER context_engineering_skill has cleaned "
            "the data. Produces Streamlit or HTML output."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "string",
                    "description": "The filtered project data to render."
                },
                "framework": {
                    "type": "string",
                    "enum": ["Streamlit", "HTML"],
                    "description": "Target UI framework for the output."
                }
            },
            "required": ["data", "framework"]
        }
    }
]


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Dispatch a tool call to the matching skill module (or stub)."""
    print(f"  [INPUT] {json.dumps(tool_input, indent=2)[:300]}...")

    if tool_name == "context_engineering_skill":
        # Real integration point: load from Agent-Skills-for-Context-Engineering
        # from context_optimization.main import optimize   (example)
        raw = tool_input.get("raw_context", "")
        return (
            f"[context_engineering_skill] Filtered {len(raw)} chars of project data.\n"
            "Key findings extracted:\n"
            "- NBKCH Stage Gate 3: PASSED (audit score 87/100)\n"
            "- BEP compliance: 94% (6 open actions)\n"
            "- LOIN matrix: 312/320 elements validated\n"
            "- Critical path delta: +3 days (facade package)\n"
            "- Open QAQC issues: 14 (3 critical, 11 minor)\n"
            "- Last CDE sync: 2026-05-16 22:41 UTC"
        )

    if tool_name == "ui_ux_pro_max_skill":
        # Real integration point: load from ui-ux-pro-max-skill/src
        # from ui_ux_pro_max.renderer import render   (example)
        data = tool_input.get("data", "")
        framework = tool_input.get("framework", "Streamlit")
        return (
            f"[ui_ux_pro_max_skill] Professional {framework} Hospital Status Report generated.\n"
            "Design spec: Clinical Precision theme — slate-900 background, "
            "cyan-400 accent, Inter/JetBrains Mono typeface pair.\n"
            "Sections rendered:\n"
            "  1. Executive KPI strip (Stage Gate badge, BEP %, LOIN progress bar)\n"
            "  2. Critical path timeline (Gantt-style, 30-day window)\n"
            "  3. QAQC issue heatmap (severity × discipline)\n"
            "  4. CDE sync status table\n"
            "  5. Action items panel (owner + due date + RAG status)\n"
            f"Source data digest: {len(data)} chars consumed."
        )

    return f"[{tool_name}] Executed with input: {json.dumps(tool_input)}"


def run_agent(prompt: str, max_turns: int = 6):
    """Agentic loop — runs until end_turn or max_turns exceeded."""
    print(f"\n[AGENT] Starting: NBKCH Hospital Status Report\n{'='*60}")

    messages = [{"role": "user", "content": prompt}]

    for turn in range(1, max_turns + 1):
        print(f"\n[TURN {turn}] Calling Claude ({client.__class__.__module__})...")

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )
        except Exception as e:
            print(f"[CRITICAL ERROR] API call failed: {e}")
            sys.exit(1)

        print(f"  stop_reason={response.stop_reason}  blocks={len(response.content)}")

        # Collect all tool calls in this response
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        # Print any text Claude produced in this turn
        for tb in text_blocks:
            print(f"\n[CLAUDE]\n{tb.text}")

        if response.stop_reason == "end_turn" or not tool_calls:
            # Done — print final response if not already printed
            if not text_blocks:
                print("\n[RESPONSE] (no text in final turn)")
            print(f"\n{'='*60}\n[AGENT] Completed in {turn} turn(s).")
            break

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # Execute every tool call and collect results
        tool_results = []
        for tc in tool_calls:
            print(f"\n[TOOL] {tc.name}")
            result = execute_tool(tc.name, tc.input)
            print(f"  [RESULT] {result[:200]}...")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result,
            })

        # Append all tool results in one user turn
        messages.append({"role": "user", "content": tool_results})

    else:
        print(f"\n[WARNING] Reached max_turns={max_turns} without end_turn.")


if __name__ == "__main__":
    user_query = (
        "Generate a Professional Hospital Status Report for the NBKCH project "
        "(NBK Children's Hospital, Kuwait — ISO 19650 BIM delivery).\n\n"
        "Workflow:\n"
        "1. Call context_engineering_skill first to filter and compress the following "
        "raw project status snapshot into only decision-critical signals:\n\n"
        "   RAW DATA:\n"
        "   - Stage Gate 3 audit completed 2026-05-14, score 87/100, 6 BEP actions open\n"
        "   - LOIN matrix: 312 of 320 elements validated, 8 pending IR sign-off\n"
        "   - CDE (Autodesk ACC) last sync 2026-05-16 22:41 UTC, 4 clashes unresolved\n"
        "   - Critical path: facade package +3 days, MEP coordination on track\n"
        "   - QAQC open issues: 14 total (3 critical/structural, 11 minor/annotation)\n"
        "   - BEP compliance score: 94%, MIDP delivery: 78% complete\n"
        "   - Next milestone: Stage Gate 4 submission 2026-06-01\n\n"
        "2. Then call ui_ux_pro_max_skill with framework='Streamlit' to render the "
        "filtered data as a polished professional dashboard report with a clinical/BIM "
        "aesthetic suitable for a client-facing hospital project status meeting."
    )

    run_agent(user_query)
