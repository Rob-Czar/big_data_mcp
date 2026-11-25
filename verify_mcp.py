"""Interactive, Ollama-free verification of four NYC restaurant MCP experiments.

Prerequisite: mongo_api.py, redis_api.py, mcp_server.py are running.
Run: python verify_all_mcp_prompts.py

Each step calls the SAME MCP tools as Ollama, shows the actual returned data,
checks key assumptions, and pauses for inspection before continuing.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from mcp import Client as MCPClient

MCP_URL = "http://127.0.0.1:5000/mcp"
REPORT_FILE = Path("manual_mcp_verification.json")

# These values come from the four earlier Ollama benchmark answers.
# They are checked against live MCP results; they are NEVER used to choose
# the next tool's borough, cuisine, or restaurant ID.
BENCHMARK = {
    "c_borough": "Bronx", "c_percent": 1.19,
    "a_borough": "Bronx", "a_percent": 84.73,
    "bronx_top_cuisine": "American", "bronx_top_count": 411,
    "brooklyn_first_id": "40364305",
    "bronx_first_id": "40357217",
}


def print_json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def decode_content(result):
    if result.is_error:
        raw = " | ".join(getattr(x, "text", str(x)) for x in result.content)
        raise RuntimeError(f"MCP returned an error: {raw}")
    pieces = [getattr(x, "text", None) for x in result.content]
    pieces = [p for p in pieces if p is not None]
    if not pieces:
        structured = getattr(result, "structured_content", None)
        if structured is None:
            raise ValueError("MCP response has no text or structured content")
        return structured
    parsed = []
    for p in pieces:
        try:
            parsed.append(json.loads(p))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Tool returned non-JSON text: {p[:300]}") from exc
    return parsed[0] if len(parsed) == 1 else parsed


def unwrap(value):
    for _ in range(5):
        if isinstance(value, str):
            try:
                value = json.loads(value)
                continue
            except json.JSONDecodeError:
                return value
        if isinstance(value, dict) and len(value) == 1:
            key = next(iter(value))
            if key in {"result", "results", "data", "items", "restaurants", "cuisines", "restaurant"}:
                value = value[key]
                continue
        return value
    return value


def records(value):
    value = unwrap(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        # Some MCP servers send one JSON object per content part.
        output = []
        for item in value:
            item = unwrap(item)
            if isinstance(item, dict):
                output.append(item)
            elif isinstance(item, list):
                output.extend(item)
            else:
                raise ValueError(f"Unexpected item in list: {item!r}")
        return output
    raise ValueError(f"Expected a dict/list, received {type(value).__name__}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def approx(a, b, tolerance=0.011):
    return abs(float(a) - float(b)) <= tolerance


def latest_grade(record):
    """Identify latest dated inspection (handles MongoDB Extended JSON dates)."""
    def to_datetime(raw):
        if isinstance(raw, dict) and "$date" in raw:
            raw = raw["$date"]
        if isinstance(raw, dict) and "$numberLong" in raw:
            raw = int(raw["$numberLong"])
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None
    dated = []
    for item in record.get("grades", []) or []:
        if isinstance(item, dict):
            dt = to_datetime(item.get("date"))
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dated.append((dt, item))
    if not dated:
        return None
    dt, grade = max(dated, key=lambda pair: pair[0])
    return {"date": dt.date().isoformat(), "grade": grade.get("grade"), "score": grade.get("score")}


class QuitVerification(Exception):
    pass


class Session:
    def __init__(self, mcp):
        self.mcp = mcp
        self.audit = []
        self.checks = []

    async def call(self, experiment, step, tool, args):
        print(f"\n{'=' * 68}\nEXPERIMENT {experiment} | STEP {step} | {tool}\n{'=' * 68}")
        print("Arguments:")
        print_json(args)
        result = await self.mcp.call_tool(tool, args)
        data = unwrap(decode_content(result))
        print("\nACTUAL MCP RESULT:")
        print_json(data)
        self.audit.append({"experiment": experiment, "step": step, "tool": tool,
                           "arguments": args, "response": data})
        return data

    def check(self, title, ok, details=""):
        print(f"\n[{'PASS' if ok else 'FAIL'}] {title}")
        if details:
            print(details)
        self.checks.append({"check": title, "passed": bool(ok), "details": details})

    def pause(self):
        choice = input("\nInspect the result. ENTER = next step | q = quit: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            raise QuitVerification()


async def experiment_1(s):
    print("\nPROMPT 1: Which borough has the highest C-grade percentage?")
    result = await s.call(1, 1, "inspection_grade_ranking", {"grade": "C"})
    require(isinstance(result, dict), "Expected grade-ranking JSON object")
    boroughs = result["boroughs"]
    highest = max(boroughs, key=lambda x: x["percentage"])
    s.check("Grade-ranking agrees with its own highest_borough field",
            result.get("highest_borough") == highest["borough"])
    s.check("Percentage matches counts (rounded to 2 decimals)",
            approx(highest["percentage"], 100 * highest["matching"] / highest["total"]))
    s.check("Matches Ollama Prompt 1 answer: Bronx, 1.19%",
            highest["borough"] == BENCHMARK["c_borough"] and
            approx(highest["percentage"], BENCHMARK["c_percent"]))
    s.pause()


async def experiment_2(s):
    print("\nPROMPT 2: Highest A-grade borough -> its top five cuisines")
    ranking = await s.call(2, 1, "inspection_grade_ranking", {"grade": "A"})
    require(isinstance(ranking, dict), "Expected grade-ranking JSON object")
    highest = max(ranking["boroughs"], key=lambda x: x["percentage"])
    borough = highest["borough"]
    s.check("Highest A-grade borough matches Ollama Prompt 2",
            borough == BENCHMARK["a_borough"] and approx(highest["percentage"], BENCHMARK["a_percent"]),
            f"Database says: {borough}, {highest['percentage']}%")
    s.pause()

    cuisines_raw = await s.call(2, 2, "top_cuisines", {"borough": borough, "limit": 5})
    cuisines = records(cuisines_raw)
    s.check("Returned five cuisines", len(cuisines) == 5)
    s.check("Cuisine counts are sorted descending",
            all(cuisines[i]["count"] >= cuisines[i + 1]["count"] for i in range(len(cuisines) - 1)))
    s.check("Leading cuisine matches Ollama Prompt 2",
            bool(cuisines) and cuisines[0].get("cuisine") == "American" and cuisines[0].get("count") == 411)
    print("\nCompare all five returned cuisines and counts with the Ollama answer.")
    s.pause()


async def experiment_3(s):
    print("\nPROMPT 3: Search Brooklyn Italian restaurants -> Redis first ID")
    search_raw = await s.call(3, 1, "search_restaurants",
                              {"borough": "Brooklyn", "cuisine": "Italian", "limit": 3})
    found = records(search_raw)
    require(found, "MongoDB search returned no restaurants")
    s.check("Search returns up to three restaurants", 1 <= len(found) <= 3)
    s.check("All restaurants are Brooklyn Italian matches",
            all(r.get("borough", "").lower() == "brooklyn" and
                "italian" in r.get("cuisine", "").lower() for r in found))
    first = found[0]
    rid = str(first["restaurant_id"])
    s.check("First ID matches Ollama Prompt 3", rid == BENCHMARK["brooklyn_first_id"],
            f"Database returned {first.get('name')} (ID {rid})")
    s.pause()

    raw = await s.call(3, 2, "get_restaurant", {"restaurant_id": rid})
    record = unwrap(raw)
    require(isinstance(record, dict), "Redis lookup did not return a record object")
    s.check("Redis ID matches MongoDB's first result", str(record.get("restaurant_id")) == rid)
    s.check("Redis name, cuisine and borough match MongoDB",
            all(record.get(k) == first.get(k) for k in ("name", "cuisine", "borough")))
    recent = latest_grade(record)
    print("\nMost recent dated inspection (computed from Redis grades):")
    print_json(recent if recent is not None else {"warning": "No parseable dated inspections"})
    s.check("Latest grade matches Ollama Prompt 3 (A, 2014-02-25)",
            recent is not None and recent["grade"] == "A" and recent["date"] == "2014-02-25")
    print("\nCompare the address shown above with the Ollama answer.")
    s.pause()


async def experiment_4(s):
    print("\nPROMPT 4: Highest C-grade borough -> cuisine -> search -> Redis")
    ranking = await s.call(4, 1, "inspection_grade_ranking", {"grade": "C"})
    require(isinstance(ranking, dict), "Expected grade-ranking JSON object")
    highest = max(ranking["boroughs"], key=lambda x: x["percentage"])
    borough = highest["borough"]
    s.check("Prompt 4 grade ranking matches Ollama",
            borough == BENCHMARK["c_borough"] and approx(highest["percentage"], BENCHMARK["c_percent"]))
    s.pause()

    cuisine_raw = await s.call(4, 2, "top_cuisines", {"borough": borough, "limit": 1})
    cuisines = records(cuisine_raw)
    require(len(cuisines) == 1, f"Expected one cuisine, received {len(cuisines)}")
    cuisine = cuisines[0]["cuisine"]
    s.check("Prompt 4 leading cuisine matches Ollama",
            cuisine == BENCHMARK["bronx_top_cuisine"] and
            cuisines[0].get("count") == BENCHMARK["bronx_top_count"])
    s.pause()

    search_raw = await s.call(4, 3, "search_restaurants",
                              {"borough": borough, "cuisine": cuisine, "limit": 3})
    found = records(search_raw)
    require(found, "MongoDB search returned no restaurants")
    s.check("Restaurant search filters and limit are respected",
            len(found) <= 3 and all(
                r.get("borough", "").lower() == borough.lower() and
                cuisine.lower() in r.get("cuisine", "").lower() for r in found))
    first = found[0]
    rid = str(first["restaurant_id"])
    s.check("Prompt 4 first restaurant ID matches Ollama", rid == BENCHMARK["bronx_first_id"],
            f"Database returned {first.get('name')} (ID {rid})")
    s.pause()

    raw = await s.call(4, 4, "get_restaurant", {"restaurant_id": rid})
    record = unwrap(raw)
    require(isinstance(record, dict), "Redis lookup did not return a record object")
    s.check("Redis record ID, name, borough, cuisine match MongoDB",
            str(record.get("restaurant_id")) == rid and
            all(record.get(k) == first.get(k) for k in ("name", "borough", "cuisine")))
    print("\nCompare the address shown above with the Ollama answer.")
    s.pause()


async def main():
    print("\nManual verification of all four Ollama/MCP benchmark prompts")
    print("Ollama is NOT used. These are direct calls to your existing MCP server.")
    print("Each call pauses so you can inspect the response before continuing.")
    try:
        async with MCPClient(MCP_URL) as mcp:
            response = await mcp.list_tools()
            names = {t.name for t in response.tools}
            required = {"inspection_grade_ranking", "top_cuisines", "search_restaurants", "get_restaurant"}
            missing = required - names
            if missing:
                print("Missing MCP tools:", ", ".join(sorted(missing)))
                return
            s = Session(mcp)
            for run in (experiment_1, experiment_2, experiment_3, experiment_4):
                try:
                    await run(s)
                except QuitVerification:
                    print("\nStopped at your request.")
                    break
                except Exception as exc:
                    print(f"\n[FAILED] {type(exc).__name__}: {exc}")
                    print("Stopping: fix this step before checking dependent steps.")
                    break
            print(f"\nChecks passed: {sum(bool(c['passed']) for c in s.checks)}/{len(s.checks)}")
            if any(not c["passed"] for c in s.checks):
                print("Some database responses differed from the recorded Ollama answers.")
            REPORT_FILE.write_text(json.dumps({
                "created_at": datetime.now(timezone.utc).isoformat(),
                "note": "Independent MCP tool calls; not independent validation of original inspection data",
                "calls": s.audit, "checks": s.checks,
            }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            print(f"Verification evidence saved: {REPORT_FILE.resolve()}")
    except Exception as exc:
        print(f"\nConnection/setup error: {type(exc).__name__}: {exc}")
        print("Verify mongo_api.py, redis_api.py and mcp_server.py are running.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")

