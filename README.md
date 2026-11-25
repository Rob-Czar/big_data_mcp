# big_data_mcp

NYC restaurant analytics using MongoDB, Redis, and the Model Context Protocol (MCP), with an Ollama client for natural-language questions and thinking-mode comparisons.

MongoDB provides restaurant searches and aggregation results. Redis stores full restaurant records for lookup by ID. A local MCP server exposes both Flask APIs as tools that the Ollama client can discover and call.

## Project files

| File | Purpose |
| --- | --- |
| `mongo_api.py` | Flask API for restaurant searches, neighborhoods, cuisine counts, and inspection-grade rankings. |
| `redis_api.py` | Flask API for retrieving a restaurant record from Redis by ID. |
| `mcp_server.py` | Exposes five tools backed by the Flask APIs. |
| `ollama_mcp.py` | Interactive Ollama client and benchmark comparing thinking on and off. |
| `transfer_restaurants.py` | Copies MongoDB restaurant documents into Redis and checks a sample record and key counts. |
| `test_mcp.py` | MCP discovery and smoke-check script; see the known limitations below. |
| `verify_mcp.py` | Interactive verification of four multi-step experiments through direct MCP calls, without Ollama. |
| `example.env` | Environment variable template; contains no credentials. |

## Prerequisites and dependencies

- A Python environment with Flask, PyMongo, redis, python-dotenv, httpx, ollama, and a compatible MCP Python package.
- MongoDB with `sample_restaurants.restaurants` and `sample_restaurants.neighborhoods` populated. Grade analytics require support for the `$sortArray` aggregation expression.
- A Redis instance reachable from your machine.
- Ollama with the `qwen3:8b` model for the interactive benchmark. Direct MCP verification does not require Ollama.

Clone the repository and create an environment (PowerShell):

```powershell
git clone https://github.com/Rob-Czar/big_data_mcp.git
cd big_data_mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install flask pymongo redis python-dotenv httpx ollama
```

**MCP compatibility:** this repository does not include a dependency lockfile or a pinned MCP package version. The code expects `MCPServer` from `mcp.server`, `Client` from `mcp`, and client objects with methods such as `list_tools()` and `call_tool()`. Install the MCP implementation/version matching those interfaces before starting the server or clients. The dependency command above does not install MCP, and installation of a package named `mcp` alone is not a verified setup for these scripts.

## Configure connections

Run commands from the repository directory so the scripts can find `.env`:

```powershell
Copy-Item example.env .env
```

Fill in the connection settings in `.env`:

| Variable | Usage |
| --- | --- |
| `MONGO_URI` | MongoDB connection URI. |
| `REDIS_HOST` | Redis hostname. |
| `REDIS_PORT` | Redis port, supplied as an integer. |
| `REDIS_USERNAME` | Redis username. Set this explicitly; an empty template value does not trigger the code's fallback of `default`. |
| `REDIS_PASSWORD` | Redis password. |
| `REDIS_TLS` | Set to `true` when your Redis connection requires TLS; the template uses `false`. |
| `MONGO_DB` | Present in the template but currently unused by the scripts. |
| `MONGO_COLLECTION` | Present in the template but currently unused by the scripts. |

The code selects the database `sample_restaurants` and collections `restaurants` and `neighborhoods` directly. Changing `MONGO_DB` or `MONGO_COLLECTION` will not change those selections. Keep your populated `.env` local and out of commits.

## Populate Redis

After MongoDB and Redis are configured, copy the restaurant records:

```powershell
python transfer_restaurants.py
```

For a smaller import:

```powershell
python transfer_restaurants.py 100
```

The script writes keys as `restaurant:<restaurant_id>`, falling back to the MongoDB document ID when `restaurant_id` is absent. It uses BSON Extended JSON to preserve document values and sends writes in batches of 100. Existing keys with the same ID are overwritten; unrelated or stale keys are not removed. It verifies one sample document and compares counts, which does not establish equality of every record. A partial import may not include IDs used by the verification scripts.

## Start the services

Open three terminals, activate the same Python environment in each, and run these commands from the repository directory:

| Terminal | Command | Address |
| --- | --- | --- |
| 1 | `python mongo_api.py` | `http://127.0.0.1:5001` |
| 2 | `python redis_api.py` | `http://127.0.0.1:5002` |
| 3 | `python mcp_server.py` | `http://127.0.0.1:5000/mcp` |

Start both Flask APIs before making MCP tool calls. Each database API checks its connection on startup. The services bind to the local machine.

## MCP tools

| Tool | Arguments | Result |
| --- | --- | --- |
| `search_restaurants` | `name=""`, `cuisine=""`, `borough=""`, `limit=10` | Matching restaurant summaries from MongoDB. |
| `top_cuisines` | `borough=""`, `limit=10` | Cuisine counts, highest first. |
| `get_restaurant` | `restaurant_id` | Full record from Redis. |
| `inspection_grade_ranking` | `grade="A"` | Grade counts and percentages by borough. |
| `search_neighborhoods` | `name=""`, `limit=10` | Matching neighborhood names. |

Restaurant and neighborhood search limits are clamped to 1–20; cuisine limits are clamped to 1–30.

Grade rankings use each restaurant's latest dated inspection. The denominator includes restaurants in the five NYC boroughs whose latest inspection grade is one of `A`, `B`, `C`, `Z`, `P`, or `Not Yet Graded`. `N` is accepted as an alias for `Not Yet Graded`. These are inspection grades, not customer ratings. The older `/analytics/grades` endpoint uses a different denominator: only latest grades A, B, or C.

## Flask endpoints

All routes use GET. MongoDB routes are on port 5001; Redis routes are on port 5002.

| Route | Query parameters |
| --- | --- |
| `/restaurants` | `name`, `cuisine`, `borough`, `limit` |
| `/neighborhoods` | `name`, `limit` |
| `/analytics/cuisines` | `borough`, `limit` |
| `/analytics/grade-ranking` | `grade`, `sort_by` (`percentage`, `matching`, or `total`) |
| `/analytics/grades` | None; legacy A-grade analysis. |
| `/count` | None; MongoDB restaurant count. |
| `/restaurants/<restaurant_id>` | None; Redis record lookup. |

Example API checks in PowerShell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:5001/count'
Invoke-RestMethod 'http://127.0.0.1:5001/analytics/grade-ranking?grade=C'
Invoke-RestMethod 'http://127.0.0.1:5001/restaurants?borough=Brooklyn&cuisine=Italian&limit=3'
```

## Run the Ollama benchmark

Ensure Ollama is running at `http://127.0.0.1:11434`, then download the configured model and start the client:

```powershell
ollama pull qwen3:8b
python ollama_mcp.py
```

Example questions:

- Which borough has the highest percentage of C-grade restaurants?
- Find the borough with the highest A-grade percentage, then show its five most common cuisines.
- Find three Italian restaurants in Brooklyn, then retrieve the first restaurant's full record from Redis.

The default mode runs each question with thinking both on and off, using fresh conversation history. The execution order alternates between comparisons. Available commands:

```text
:mode compare
:mode on
:mode off
exit
```

After each question, optionally rate answer accuracy as 0 (incorrect), 1 (partially correct), or 2 (correct). Ratings are manual and happen after timing. Results append to `ollama_benchmark_results.csv`, including timings, tool calls/errors, token counts, generation speed, answers, and ratings. There is a limit of six model rounds per trial. The model and service URLs are constants in `ollama_mcp.py`.

## Verify MCP behavior without Ollama

With all three services running:

```powershell
python verify_mcp.py
```

The script runs four experiments covering grade ranking, cuisine aggregation, restaurant searches, and dependent Redis lookups. It prints actual tool responses and pauses at each step: press Enter to continue or `q` to stop. It writes collected responses and checks to `manual_mcp_verification.json`.

Some checks compare results against recorded benchmark values and IDs. Different source data or ordering can produce mismatches. This verifies MCP responses and consistency between steps; it does not independently validate the original inspection data. The script's introductory docstring mentions `verify_all_mcp_prompts.py`; the actual filename in this repository is `verify_mcp.py`.

## Known limitations

- `test_mcp.py` still requests `inspection_grades_by_borough`, which is not registered by the current server. That check will report `FAILED: Tool not registered`. The current grade tool is `inspection_grade_ranking`; use `verify_mcp.py` for its documented checks.
- `test_mcp.py` also looks up a fixed Redis restaurant ID (`40356018`), which must exist for that lookup to succeed.
- Dependency versions are not pinned, and the MCP interfaces need a matching implementation. A complete installation and end-to-end run have not been validated as part of this README review.
- Dataset files, populated credentials, and generated benchmark/verification reports are not included in the repository.
