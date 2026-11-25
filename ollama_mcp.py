import asyncio
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from ollama import AsyncClient
from mcp import Client as MCPClient


# --------------------------------------------------
# Configuration
# --------------------------------------------------

MODEL = "qwen3:8b"
OLLAMA_URL = "http://127.0.0.1:11434"
MCP_URL = "http://127.0.0.1:5000/mcp"
OLLAMA_TIMEOUT = 300.0
MAX_TOOL_ROUNDS = 6
RESULTS_FILE = Path("ollama_benchmark_results.csv")

ALLOWED_TOOLS = {
    "search_restaurants",
    "search_neighborhoods",
    "top_cuisines",
    "inspection_grade_ranking",
    "get_restaurant",
}

TOOL_DATABASES = {
    "search_restaurants": "MongoDB",
    "search_neighborhoods": "MongoDB",
    "top_cuisines": "MongoDB",
    "inspection_grade_ranking": "MongoDB",
    "get_restaurant": "Redis",
}

SYSTEM_PROMPT = """
You analyze NYC restaurant data using MongoDB Atlas and Redis Cloud
through the provided MCP tools. Use actual tool results, not guesses.

Use search_restaurants to search by name, cuisine, or borough.
Use search_neighborhoods for neighborhood name searches.
Use top_cuisines for cuisine counts and rankings.
Use inspection_grade_ranking for inspection grade counts and percentages.
Use get_restaurant for Redis lookups by restaurant ID.

For a request that needs a restaurant's full record but gives only its
name, first search MongoDB for its ID, then retrieve that ID from Redis.
When later tool calls depend on earlier ones, use the exact returned
borough, cuisine, or restaurant ID. Prefer one dependent tool call at a
time and check its results before making the next call.

Health inspection grades are not customer star ratings. Grade codes
include A, B, C, Z, P, and Not Yet Graded; Z means Grade Pending.
Distinguish grade percentages, matching-grade restaurant counts, and
total eligible restaurant counts. Pass only supported tool arguments.
Do not invent numbers, names, IDs, or tool results. If a tool fails,
explain the limitation rather than pretending it succeeded.
Answer clearly and concisely with the evidence returned by the tools.
""".strip()

CSV_COLUMNS = [
    "timestamp", "model", "thinking", "question", "completed",
    "accuracy_score", "total_seconds", "ollama_seconds",
    "mcp_seconds", "ollama_server_seconds", "model_calls",
    "tool_calls", "tool_errors", "tools_used", "generated_tokens",
    "prompt_tokens", "generation_tokens_per_second", "error", "answer",
]


def database_for(tool_name):
    return TOOL_DATABASES.get(tool_name, "Unknown")


def read_tool_output(result):
    content = "\n".join(
        getattr(item, "text", str(item)) for item in result.content
    )
    if result.is_error:
        return "Tool error: " + content
    return content or "Tool returned no content."


async def run_trial(ollama, mcp, tools, available, question, thinking):
    """Run ONE question with fresh history and collect timing/tool stats."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    stats = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": MODEL,
        "thinking": "ON" if thinking else "OFF",
        "question": question,
        "completed": False,
        "accuracy_score": "",
        "total_seconds": 0.0,
        "ollama_seconds": 0.0,
        "mcp_seconds": 0.0,
        "ollama_server_seconds": 0.0,
        "model_calls": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "tools_used": "",
        "generated_tokens": 0,
        "prompt_tokens": 0,
        "generation_tokens_per_second": 0.0,
        "error": "",
        "answer": "",
    }
    used_tools = []
    generation_duration_ns = 0
    started = time.perf_counter()

    print(f"\n{'=' * 54}")
    print(f"THINKING {stats['thinking']}  |  {MODEL}")
    print(f"{'=' * 54}")

    for _ in range(MAX_TOOL_ROUNDS):
        model_started = time.perf_counter()
        try:
            response = await ollama.chat(
                model=MODEL,
                messages=messages,
                tools=tools,
                think=thinking,
                options={"temperature": 0},
            )
        except httpx.TimeoutException:
            stats["error"] = "Ollama timed out"
            stats["ollama_seconds"] += time.perf_counter() - model_started
            break
        except Exception as exc:
            stats["error"] = f"Ollama error: {type(exc).__name__}: {exc}"
            stats["ollama_seconds"] += time.perf_counter() - model_started
            break

        stats["ollama_seconds"] += time.perf_counter() - model_started
        stats["model_calls"] += 1
        stats["ollama_server_seconds"] += (
            (getattr(response, "total_duration", None) or 0) / 1e9
        )
        stats["generated_tokens"] += getattr(response, "eval_count", None) or 0
        stats["prompt_tokens"] += getattr(response, "prompt_eval_count", None) or 0
        generation_duration_ns += getattr(response, "eval_duration", None) or 0

        messages.append(response.message)
        calls = response.message.tool_calls or []

        if not calls:
            stats["answer"] = (response.message.content or "").strip()
            stats["completed"] = bool(stats["answer"])
            if not stats["completed"]:
                stats["error"] = "Model returned an empty final answer"
            break

        for call in calls:
            name = call.function.name
            arguments = call.function.arguments or {}
            stats["tool_calls"] += 1
            used_tools.append(name)

            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = None

            print(f"\n[DATABASE] {database_for(name)}")
            print(f"[TOOL] {name}")
            print(f"[ARGUMENTS] {arguments}")

            if name not in available:
                output = f"Tool {name} is not permitted or is unavailable."
                stats["tool_errors"] += 1
                print("[STATUS] BLOCKED")
            elif not isinstance(arguments, dict):
                output = "Tool error: Arguments must be a JSON object."
                stats["tool_errors"] += 1
                print("[STATUS] INVALID ARGUMENTS")
            else:
                tool_started = time.perf_counter()
                try:
                    result = await mcp.call_tool(name, arguments)
                    output = read_tool_output(result)
                    if result.is_error:
                        stats["tool_errors"] += 1
                        print("[STATUS] ERROR")
                    else:
                        print("[STATUS] SUCCESS")
                except Exception as exc:
                    stats["tool_errors"] += 1
                    output = f"Tool failed: {type(exc).__name__}: {exc}"
                    print(f"[STATUS] ERROR: {exc}")
                finally:
                    stats["mcp_seconds"] += time.perf_counter() - tool_started

            messages.append({
                "role": "tool",
                "tool_name": name,
                "content": output,
            })
    else:
        stats["error"] = f"Reached the limit of {MAX_TOOL_ROUNDS} model rounds"

    stats["total_seconds"] = round(time.perf_counter() - started, 3)
    for field in ("ollama_seconds", "mcp_seconds", "ollama_server_seconds"):
        stats[field] = round(stats[field], 3)
    stats["tools_used"] = ", ".join(used_tools)
    if generation_duration_ns:
        stats["generation_tokens_per_second"] = round(
            stats["generated_tokens"] * 1e9 / generation_duration_ns, 2
        )

    print("\n[ANSWER]")
    print(stats["answer"] or f"No completed answer. {stats['error']}")
    print("\n[MEASUREMENTS]")
    print(f"Total end-to-end time: {stats['total_seconds']:.3f} s")
    print(f"Ollama client time:    {stats['ollama_seconds']:.3f} s")
    print(f"MCP tool time:         {stats['mcp_seconds']:.3f} s")
    print(f"Model API calls:       {stats['model_calls']}")
    print(f"MCP tool calls:        {stats['tool_calls']}")
    print(f"Tool errors:           {stats['tool_errors']}")
    print(f"Generated tokens:      {stats['generated_tokens']}")
    print(
        "Generation speed:     "
        f"{stats['generation_tokens_per_second']:.2f} tokens/s"
    )
    return stats


def ask_accuracy(result):
    print(f"\nRate THINKING {result['thinking']} answer:")
    print("0 = incorrect, 1 = partially correct, 2 = correct")
    while True:
        try:
            rating = input("Accuracy score (Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if rating in ("", "0", "1", "2"):
            result["accuracy_score"] = rating
            return
        print("Please enter 0, 1, 2, or leave it blank.")


def save_results(rows):
    add_header = not RESULTS_FILE.exists() or RESULTS_FILE.stat().st_size == 0
    with RESULTS_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if add_header:
            writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved results to: {RESULTS_FILE.resolve()}")


def show_comparison(results):
    by_mode = {row["thinking"]: row for row in results}
    print("\n" + "=" * 54)
    print("COMPARISON: SAME QUESTION, FRESH CONTEXT")
    print("=" * 54)
    print(f"{'Metric':<25} {'ON':>12} {'OFF':>12}")
    for label, key in [
        ("Total seconds", "total_seconds"),
        ("Ollama seconds", "ollama_seconds"),
        ("MCP seconds", "mcp_seconds"),
        ("Model calls", "model_calls"),
        ("Tool calls", "tool_calls"),
        ("Tool errors", "tool_errors"),
        ("Generated tokens", "generated_tokens"),
        ("Generation tokens/s", "generation_tokens_per_second"),
        ("Accuracy score (0-2)", "accuracy_score"),
    ]:
        print(
            f"{label:<25} "
            f"{str(by_mode['ON'][key]):>12} "
            f"{str(by_mode['OFF'][key]):>12}"
        )

    on = by_mode["ON"]["total_seconds"]
    off = by_mode["OFF"]["total_seconds"]
    if on > 0 and off > 0:
        faster = "ON" if on < off else "OFF"
        if on == off:
            print("\nThe two modes took the same measured time.")
        else:
            ratio = max(on, off) / min(on, off)
            print(f"\nThinking {faster} was {ratio:.2f}x faster for this question.")
    print("Accuracy scores are your ratings, not automatic verification.")


async def main():
    ollama = AsyncClient(host=OLLAMA_URL, timeout=OLLAMA_TIMEOUT)

    async with MCPClient(MCP_URL) as mcp:
        response = await mcp.list_tools()
        available = {
            tool.name: tool
            for tool in response.tools
            if tool.name in ALLOWED_TOOLS
        }
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
            for tool in available.values()
        ]

        if not tools:
            print("No supported MCP tools found. Check that mcp_server.py is running.")
            return

        print("\nNYC Restaurant Ollama Benchmark")
        print(f"Model: {MODEL}")
        print("Available tools:")
        for name in available:
            print(f"  {name} [{database_for(name)}]")

        missing = ALLOWED_TOOLS - set(available)
        if missing:
            print("Unavailable tools:", ", ".join(sorted(missing)))

        mode = "compare"
        comparisons = 0
        print("\nCommands: :mode compare | :mode on | :mode off | exit")
        print("Each question uses a fresh conversation for fair testing.")
        print("Default mode: compare (runs BOTH settings)")

        while True:
            try:
                prompt = input(f"\nYou [{mode}]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not prompt:
                continue
            if prompt.lower() in {"exit", "quit", ":exit", ":quit"}:
                break

            if prompt.lower().startswith(":mode "):
                requested = prompt[6:].strip().lower()
                if requested in {"on", "off", "compare"}:
                    mode = requested
                    print(f"Mode set to: {mode}")
                else:
                    print("Choose :mode on, :mode off, or :mode compare")
                continue

            if prompt.startswith(":"):
                print("Commands: :mode compare | :mode on | :mode off | exit")
                continue

            if mode == "compare":
                # Alternate execution order between comparisons to reduce
                # the bias from whichever mode always runs first.
                order = [True, False] if comparisons % 2 == 0 else [False, True]
                comparisons += 1
            else:
                order = [mode == "on"]

            results = []
            for thinking in order:
                result = await run_trial(
                    ollama, mcp, tools, available, prompt, thinking
                )
                results.append(result)

            # Ratings happen AFTER timing, so human input does not affect speed.
            for result in sorted(results, key=lambda x: x["thinking"] == "OFF"):
                ask_accuracy(result)

            if len(results) == 2:
                show_comparison(results)
            save_results(results)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"\nFatal error: {type(exc).__name__}: {exc}")

