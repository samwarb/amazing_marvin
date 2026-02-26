import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from openai import OpenAI

# ─── API KEYS ─────────────────────────────────────────────────────────────────
MARVIN_API_TOKEN         = os.getenv("MARVIN_API_TOKEN")
MARVIN_FULL_ACCESS_TOKEN = os.getenv("MARVIN_FULL_ACCESS_TOKEN")
OPENAI_API_KEY           = os.getenv("OPENAI_API_KEY")
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL     = "https://serv.amazingmarvin.com/api"
READ_HEADERS = {"X-API-Token": MARVIN_API_TOKEN}
# /api/doc requires the full access token even for reads
DOC_HEADERS  = {"X-Full-Access-Token": MARVIN_FULL_ACCESS_TOKEN}
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# gpt-4.1-nano pricing (per 1M tokens)
COST_PER_1M_INPUT  = 0.100  # USD
COST_PER_1M_OUTPUT = 0.400  # USD

usage_log = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


# ─── QUERY ACQUISITION ────────────────────────────────────────────────────────

def get_query() -> str:
    # Priority 1: environment variable (set by GitHub Actions input)
    query = os.getenv("SEARCH_QUERY", "").strip()
    if query:
        return query
    # Priority 2: command-line argument
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()
    # Priority 3: interactive prompt (local / bat file fallback)
    return input("What do you want to know? ").strip()


# ─── API FUNCTIONS (read-only) ────────────────────────────────────────────────

def get_all_projects() -> list:
    resp = requests.get(f"{BASE_URL}/categories", headers=READ_HEADERS, timeout=15)
    resp.raise_for_status()
    return [p for p in resp.json() if not p.get("done", False)]


def get_project_doc(project_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/doc?id={project_id}", headers=DOC_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_project_children(project_id: str) -> list:
    resp = requests.get(
        f"{BASE_URL}/children?parentId={project_id}",
        headers=READ_HEADERS, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


# ─── USAGE TRACKING ───────────────────────────────────────────────────────────

def track_usage(response):
    usage_log["prompt_tokens"]     += response.usage.prompt_tokens
    usage_log["completion_tokens"] += response.usage.completion_tokens
    usage_log["calls"]             += 1


# ─── PHASE 1: Identify relevant projects ─────────────────────────────────────

def identify_relevant_projects(query: str, projects: list) -> list:
    roster = "\n".join(
        f"ID: {p['_id']} | Title: {p['title']}"
        for p in projects
    )

    response = openai_client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a search assistant for a personal task manager. "
                    "Given a user query and a list of projects, identify which projects "
                    "are most likely to contain the answer. "
                    "Return ONLY a JSON array of the relevant project IDs (strings), "
                    "ordered by relevance, maximum 5 items. "
                    "If no project seems relevant, return an empty array []. "
                    "Return valid JSON only — no explanation, no markdown."
                )
            },
            {
                "role": "user",
                "content": f"Query: {query}\n\nProjects:\n{roster}"
            }
        ],
        temperature=0
    )
    track_usage(response)

    raw = response.choices[0].message.content.strip()
    try:
        ids = json.loads(raw)
        if not isinstance(ids, list):
            ids = []
    except json.JSONDecodeError:
        ids = []

    id_to_proj = {p["_id"]: p for p in projects}
    return [id_to_proj[i] for i in ids if i in id_to_proj]


# ─── PHASE 2: Gather full content for each relevant project ───────────────────

def _fetch_one_project(proj: dict) -> dict:
    pid   = proj["_id"]
    title = proj.get("title", "Unknown")

    try:
        doc  = get_project_doc(pid)
        note = doc.get("note", "").strip()
    except Exception as e:
        print(f"  Warning: could not fetch doc for '{title}': {e}")
        note = proj.get("note", "").strip()

    try:
        children = get_project_children(pid)
        tasks    = [c for c in children if c.get("db") == "Tasks"]
    except Exception as e:
        print(f"  Warning: could not fetch children for '{title}': {e}")
        tasks = []

    return {"id": pid, "title": title, "note": note, "tasks": tasks}


def gather_project_content(projects: list) -> list:
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one_project, p): p for p in projects}
        results = [f.result() for f in as_completed(futures)]
    # Restore original order from identify_relevant_projects
    order = {p["_id"]: i for i, p in enumerate(projects)}
    return sorted(results, key=lambda r: order[r["id"]])


# ─── PHASE 3: Generate an answer from gathered content ───────────────────────

def generate_answer(query: str, content_blocks: list) -> str:
    if not content_blocks:
        return "No relevant projects were found for your query."

    context_parts = []
    for block in content_blocks:
        section = [f"== PROJECT: {block['title']} =="]

        if block["note"]:
            section.append(f"Notes:\n{block['note']}")
        else:
            section.append("Notes: (none)")

        if block["tasks"]:
            task_lines = []
            for t in block["tasks"]:
                status    = "DONE" if t.get("done") else "active"
                task_note = t.get("note", "").strip()
                line      = f"  - [{status}] {t.get('title', '')}"
                if task_note:
                    line += f"\n    Note: {task_note}"
                task_lines.append(line)
            section.append("Tasks:\n" + "\n".join(task_lines))
        else:
            section.append("Tasks: (none)")

        context_parts.append("\n".join(section))

    full_context = "\n\n".join(context_parts)

    response = openai_client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a personal assistant with access to a user's task manager data. "
                    "Answer the user's question directly and concisely using ONLY the information "
                    "provided below. "
                    "If the answer is clearly present, state it plainly. "
                    "If the information is not found in the provided data, say so explicitly — "
                    "do not guess or hallucinate. "
                    "Format your answer for readability in a terminal or log output."
                )
            },
            {
                "role": "user",
                "content": f"Question: {query}\n\nTask manager data:\n{full_context}"
            }
        ],
        temperature=0
    )
    track_usage(response)

    return response.choices[0].message.content.strip()


# ─── OUTPUT HELPERS ───────────────────────────────────────────────────────────

def print_usage_summary():
    input_cost  = (usage_log["prompt_tokens"]     / 1_000_000) * COST_PER_1M_INPUT
    output_cost = (usage_log["completion_tokens"] / 1_000_000) * COST_PER_1M_OUTPUT
    total_cost  = input_cost + output_cost
    print(f"""
OpenAI Usage:
  API calls:      {usage_log['calls']}
  Input tokens:   {usage_log['prompt_tokens']:,}
  Output tokens:  {usage_log['completion_tokens']:,}
  Estimated cost: ${total_cost:.6f} USD (~\u00a3{total_cost * 0.79:.6f})
""")


def write_github_summary(query: str, relevant: list, answer: str):
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return

    input_cost  = (usage_log["prompt_tokens"]     / 1_000_000) * COST_PER_1M_INPUT
    output_cost = (usage_log["completion_tokens"] / 1_000_000) * COST_PER_1M_OUTPUT
    total_cost  = input_cost + output_cost

    project_names = ", ".join(p["title"] for p in relevant) if relevant else "None"

    lines = [
        "## Marvin Search Result",
        "",
        f"**Query:** {query}",
        "",
        f"**Projects searched:** {project_names}",
        "",
        "### Answer",
        "",
        answer,
        "",
        "---",
        f"*OpenAI calls: {usage_log['calls']} | "
        f"Tokens: {usage_log['prompt_tokens']:,} in / "
        f"{usage_log['completion_tokens']:,} out | "
        f"Cost: ${total_cost:.6f} USD (~\u00a3{total_cost * 0.79:.6f})*",
    ]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    query = get_query()
    if not query:
        print("Error: no search query provided.")
        sys.exit(1)

    print(f"Search query: {query}")
    print("-" * 60)

    # Phase 1: fetch all projects and identify relevant ones
    print("Phase 1: Fetching all projects...")
    all_projects = get_all_projects()
    print(f"  Found {len(all_projects)} active projects.")

    print("Phase 1: Identifying relevant projects via LLM...")
    relevant = identify_relevant_projects(query, all_projects)

    if not relevant:
        no_match_answer = "No relevant projects were identified for this query."
        print("\nResult: " + no_match_answer)
        print_usage_summary()
        write_github_summary(query, [], no_match_answer)
        if os.getenv("GITHUB_ACTIONS"):
            from datetime import datetime, timezone
            with open("last_result.json", "w") as _f:
                json.dump({"query": query, "answer": no_match_answer,
                           "timestamp": datetime.now(timezone.utc).isoformat(), "projects": []}, _f, indent=2)
        sys.exit(0)

    print(f"  Relevant projects: {[p['title'] for p in relevant]}")

    # Phase 2: gather full notes + tasks for each relevant project
    print("\nPhase 2: Gathering full content for relevant projects...")
    content_blocks = gather_project_content(relevant)

    # Phase 3: answer the question
    print("\nPhase 3: Generating answer...")
    answer = generate_answer(query, content_blocks)

    print("\n" + "=" * 60)
    print("ANSWER")
    print("=" * 60)
    print(answer)
    print("=" * 60)

    print_usage_summary()
    write_github_summary(query, relevant, answer)

    if os.getenv("GITHUB_ACTIONS"):
        from datetime import datetime, timezone
        with open("last_result.json", "w") as _f:
            json.dump({"query": query, "answer": answer,
                       "timestamp": datetime.now(timezone.utc).isoformat(),
                       "projects": [p["title"] for p in relevant]}, _f, indent=2)


if __name__ == "__main__":
    main()
