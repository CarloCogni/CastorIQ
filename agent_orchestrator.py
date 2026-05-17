import os
import sys
import json
from openai import OpenAI

# 1. Central Skills Repository Path Configuration
SKILLS_BASE_PATH = r"D:\Resources\01 Zigurate\02 AI\Claude skills"

# Ensure path exists and is injected to sys.path safely
if os.path.exists(SKILLS_BASE_PATH) and SKILLS_BASE_PATH not in sys.path:
    sys.path.append(SKILLS_BASE_PATH)

# 2. Initialize Local Ollama Client (No paid API key required)
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"  # Ollama requires any non-empty string here to bypass auth
)

# 3. Define the Tool Schemas (The Catalog for Qwen to understand your skills)
tools = [
    {
        "type": "function",
        "function": {
            "name": "context_engineering_skill",
            "description": "Optimizes, compresses, and structures complex engineering data or massive BIM/IFC datasets before analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_context": {"type": "string", "description": "The unformatted, raw data or report text."}
                },
                "required": ["raw_context"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_ux_pro_max_skill",
            "description": "Converts processed technical reports and structural datasets into professional dashboards (e.g., Streamlit, HTML).",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "The filtered data to visualize."},
                    "framework": {"type": "string", "enum": ["Streamlit", "HTML"],
                                  "description": "Target UI framework."}
                },
                "required": ["data", "framework"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_slop_skill",
            "description": "Cleans text outputs by removing all AI fluff, conversational filler words, and narrative padding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "The raw AI response text containing conversational filler words."}
                },
                "required": ["text"]
            }
        }
    }
]


# 4. Tool Execution Dispatcher
def execute_tool(name, arguments):
    """
    Executes the matching local technical asset from your central skills folder.
    """
    print(f"\n[LOCAL EXECUTION] Running local tool: {name}")
    print(f"[INPUTS] Arguments: {json.dumps(arguments, indent=2)}")

    try:
        # Dynamic routing placeholder for your custom modular setup
        if name == "context_engineering_skill":
            raw_text = arguments.get("raw_context", "")
            # Production pipeline logic link (Can be replaced with direct script imports):
            processed = f"--- [PROCESSED CONTEXT SNAPSHOT] ---\nFiltered 90% project clutter. Extracted key parameters, LOD 400 statuses, and metadata: {raw_text}"
            return processed

        elif name == "ui_ux_pro_max_skill":
            data_in = arguments.get("data", "")
            framework = arguments.get("framework", "Streamlit")
            ui_output = f"--- [{framework.upper()} DASHBOARD CONFIG] ---\nimport streamlit as st\nst.title('NBK Hospital - Project Status')\nst.markdown('''{data_in}''')\nst.metric(label='Model Health Index', value='98.4%')"
            return ui_output

        elif name == "stop_slop_skill":
            text_in = arguments.get("text", "")
            # Clean AI fluff programmatically
            cleaned = text_in.replace("Sure, here is the report:", "").replace("I hope this helps!", "").strip()
            return f"--- [CLEANED PROFESSIONAL OUTPUT] ---\n{cleaned}"

        else:
            return f"Error: Tool {name} is registered but has no execution logic inside the dispatcher."

    except Exception as e:
        return f"Execution failure inside {name}: {str(e)}"


# 5. Full Multi-Turn Agentic Loop Execution Engine
def run_agentic_loop(prompt):
    print("\n" + "=" * 60)
    print(f"[AGENT] Initializing Local Multi-Turn Loop via Qwen2.5-Coder...")
    print("=" * 60)

    # Initialize conversation state
    messages = [{"role": "user", "content": prompt}]
    max_turns = 6

    for turn in range(1, max_turns + 1):
        print(f"\n[TURN {turn}] Invoking Local LLM...")

        try:
            response = client.chat.completions.create(
                model="qwen2.5-coder:7b",
                messages=messages,
                tools=tools,
                tool_choice="auto"
            )

            assistant_message = response.choices[0].message
            tool_calls = assistant_message.tool_calls

            # Format message correctly to feed it back into history
            msg_dict = {"role": "assistant"}
            if assistant_message.content:
                msg_dict["content"] = assistant_message.content
            if tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in tool_calls
                ]
            messages.append(msg_dict)

            # If the model didn't request any tools, the loop is completed
            if not tool_calls:
                print(f"\n[FINAL RESPONSE FROM LOCAL ENGINE]:")
                print(assistant_message.content)
                break

            # If tool calls are present, process each tool sequentially
            print(f"[DECISION] Local LLM requested {len(tool_calls)} tool execution(s).")
            for tool_call in tool_calls:
                t_name = tool_call.function.name
                t_args = json.loads(tool_call.function.arguments)
                t_id = tool_call.id

                # Execute locally on your machine
                result_string = execute_tool(t_name, t_args)

                # Feed the execution result back into the model's context window
                messages.append({
                    "role": "tool",
                    "tool_call_id": t_id,
                    "name": t_name,
                    "content": result_string
                })

        except Exception as e:
            print(f"\n[CRITICAL SYSTEM ERROR] Loop failed on Turn {turn}: {str(e)}")
            break
    else:
        print(f"\n[WARN] Reached maximum turn limit ({max_turns}) without explicit closing.")


if __name__ == "__main__":
    # Review scenario for Gantt chart fixes in the 4D BIM module
    test_scenario = (
        "Review these Gantt chart fixes for a 4D BIM module. "
        "Check if the sticky column approach is correct and if the CPM "
        "float=0 logic is sound. Suggest any improvements. "
        "Code: [paste the modified JS sections here]"
    )

    run_agentic_loop(test_scenario)