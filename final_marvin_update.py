import requests
import time
import os
from datetime import datetime
from openai import OpenAI

# ─── API KEYS ─────────────────────────────────────────────────────────────────
# ─── API KEYS ─────────────────────────────────────────────────────────────────
MARVIN_API_TOKEN         = os.getenv("MARVIN_API_TOKEN")
MARVIN_FULL_ACCESS_TOKEN = os.getenv("MARVIN_FULL_ACCESS_TOKEN")
OPENAI_API_KEY           = os.getenv("OPENAI_API_KEY")
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL      = "https://serv.amazingmarvin.com/api"
TODAY         = datetime.now().strftime("%Y-%m-%d")
openai_client = OpenAI(api_key=OPENAI_API_KEY)

READ_HEADERS  = {"X-API-Token": MARVIN_API_TOKEN}
WRITE_HEADERS = {
    "X-Full-Access-Token": MARVIN_FULL_ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ─── USAGE TRACKER ────────────────────────────────────────────────────────────
usage_log = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

# gpt-4.1-nano pricing (per 1M tokens)
COST_PER_1M_INPUT  = 0.100  # USD
COST_PER_1M_OUTPUT = 0.400  # USD


# ─── FETCH FUNCTIONS ──────────────────────────────────────────────────────────

def get_today_items_raw():
    """Return raw todayItems (tasks, projects, etc., completed + incomplete)."""
    resp = requests.get(
        f"{BASE_URL}/todayItems",
        headers={**READ_HEADERS, "X-Date": TODAY}
    )
    resp.raise_for_status()
    return resp.json()


def get_today_tasks_incomplete():
    """Only incomplete tasks (for title + note tidy + date fixes)."""
    items = get_today_items_raw()
    return [
        i for i in items
        if i.get("db") == "Tasks" and not i.get("done", False)
    ]


def get_today_tasks_all_for_projects():
    """All tasks scheduled today, completed and incomplete, for project note scope."""
    items = get_today_items_raw()
    return [
        i for i in items
        if i.get("db") == "Tasks"
    ]


def get_inbox_tasks():
    resp = requests.get(
        f"{BASE_URL}/children?parentId=unassigned",
        headers=READ_HEADERS
    )
    resp.raise_for_status()
    return [
        i for i in resp.json()
        if i.get("db") == "Tasks" and not i.get("done", False)
    ]


def get_all_categories():
    resp = requests.get(f"{BASE_URL}/categories", headers=READ_HEADERS)
    resp.raise_for_status()
    return resp.json()


def get_doc(doc_id: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/doc?id={doc_id}",
        headers=WRITE_HEADERS
    )
    resp.raise_for_status()
    return resp.json()


# ─── UPDATE FUNCTION ──────────────────────────────────────────────────────────

def update_doc(item_id: str, setters: list):
    now_ms = int(time.time() * 1000)
    keys_being_set = [s["key"] for s in setters]
    if "updatedAt" not in keys_being_set:
        setters.append({"key": "updatedAt", "val": now_ms})
    resp = requests.post(
        f"{BASE_URL}/doc/update",
        headers=WRITE_HEADERS,
        json={"itemId": item_id, "setters": setters}
    )
    resp.raise_for_status()
    return resp.json()


# ─── AI FUNCTIONS ─────────────────────────────────────────────────────────────

def track_usage(response):
    usage_log["prompt_tokens"]     += response.usage.prompt_tokens
    usage_log["completion_tokens"] += response.usage.completion_tokens
    usage_log["calls"]             += 1


def fix_spelling(text: str) -> str:
    if not text or not text.strip():
        return text
    response = openai_client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a task title editor. Fix spelling, capitalisation and grammar only. "
                    "Return ONLY the corrected title — no explanation, no quotes. "
                    "If already correct, return it unchanged."
                )
            },
            {"role": "user", "content": text}
        ],
        temperature=0
    )
    track_usage(response)
    return response.choices[0].message.content.strip()


def tidy_note(note: str, context_title: str) -> str:
    if not note or not note.strip():
        return note

    stripped = note.strip()
    lower = stripped.lower()

    # 1) If the entire note is basically just an image / screenshot reference, skip it
    if (
        # single markdown image line
        (stripped.startswith("![") and "](" in stripped and stripped.endswith(")"))
        # or just an image URL
        or stripped.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        # or a very short "screenshot" stub
        or ("screenshot" in lower and len(lower.split()) <= 5)
    ):
        return note

    # 2) Mixed notes (text + images/links) → clean the text, leave images/links intact
    response = openai_client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional note-taker. Tidy up the following note by:\n"
                    "1. Fixing spelling, grammar or punctuation mistakes in the WRITTEN TEXT.\n"
                    "2. Adding relevant emojis at the start of key text lines or sections.\n"
                    "3. Improving formatting — use bullet points where appropriate, clear line breaks.\n"
                    "4. Keeping ALL original meaning and content — do NOT remove or invent information.\n"
                    "5. Do NOT change or remove any image links, markdown image tags, or URLs.\n"
                    "6. If there is a markdown image like ![...](...), keep that line exactly as it is.\n\n"
                    "Return ONLY the tidied note. No explanation, no preamble."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Context (task/project title): {context_title}\n\n"
                    "Tidy the text in this note, but do not change any images or links:\n\n"
                    f"{note}"
                )
            }
        ],
        temperature=0.3
    )
    track_usage(response)
    return response.choices[0].message.content.strip()


def assign_project(task_title: str, projects: list, admin_id: str) -> tuple[str, str]:
    project_list = "\n".join(
        [f"- ID: {p['_id']} | Name: {p['title']}" for p in projects]
    )
    response = openai_client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a task organiser. Given a task title and a list of projects, "
                    "return the single best matching project ID. "
                    "If unsure, reply with exactly: UNSURE. "
                    "Return ONLY the project ID or UNSURE — nothing else."
                )
            },
            {
                "role": "user",
                "content": f"Task: {task_title}\n\nProjects:\n{project_list}"
            }
        ],
        temperature=0
    )
    track_usage(response)

    answer = response.choices[0].message.content.strip()
    if answer == "UNSURE":
        admin = next((p for p in projects if p["_id"] == admin_id), None)
        return admin_id, (admin["title"] if admin else "Admin")
    matched = next((p for p in projects if p["_id"] == answer), None)
    if matched:
        return matched["_id"], matched["title"]
    admin = next((p for p in projects if p["_id"] == admin_id), None)
    return admin_id, (admin["title"] if admin else "Admin")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():

    # ── STEP 1: Fetch projects/categories ────────────────────────────────────
    print("📂  Fetching all projects and categories...")
    categories = get_all_categories()
    projects   = [c for c in categories if not c.get("done", False)]

    admin_project = next(
        (p for p in projects if p.get("title", "").strip().lower() == "admin"),
        None
    )
    if not admin_project:
        print("⚠️   No 'Admin' project found — unrecognised inbox tasks will be skipped.")
    else:
        print(f"  ✔  Admin project found: '{admin_project['title']}' (ID: {admin_project['_id']})")
    admin_id = admin_project["_id"] if admin_project else None

    # ── STEP 2: Today's tasks (incomplete only) ──────────────────────────────
    print(f"\n🔍  Fetching today's tasks ({TODAY})...")
    today_tasks_incomplete = get_today_tasks_incomplete()
    print(f"📋  Found {len(today_tasks_incomplete)} incomplete task(s) for today.\n")

    spell_fixed    = 0
    date_assigned  = 0
    notes_tidied   = 0

    for task in today_tasks_incomplete:
        task_id  = task.get("_id", "")
        original = task.get("title", "")
        day      = task.get("day")
        note     = task.get("note", "")
        setters  = []
        now_ms   = int(time.time() * 1000)

        # Fix title
        if original:
            fixed = fix_spelling(original)
            if fixed != original:
                print(f"  ✏️  SPELL FIX  | BEFORE: {original}")
                print(f"                  AFTER:  {fixed}")
                setters += [
                    {"key": "title",              "val": fixed},
                    {"key": "fieldUpdates.title", "val": now_ms}
                ]
                spell_fixed += 1
            else:
                print(f"  ✔  OK         | {original}")

        # Assign today's date if missing
        if not day:
            print(f"  📅  DATE FIX   | '{original}' → assigned {TODAY}")
            setters += [
                {"key": "day",              "val": TODAY},
                {"key": "fieldUpdates.day", "val": now_ms},
                {"key": "firstScheduled",   "val": TODAY}
            ]
            date_assigned += 1

        # Tidy task note
        if note and note.strip():
            tidied = tidy_note(note, original)
            if tidied != note:
                print(f"  📝  NOTE FIX   | Task: '{original}'")
                print(f"      BEFORE: {note[:80]}{'...' if len(note) > 80 else ''}")
                print(f"      AFTER:  {tidied[:80]}{'...' if len(tidied) > 80 else ''}")
                setters += [
                    {"key": "note",              "val": tidied},
                    {"key": "fieldUpdates.note", "val": now_ms}
                ]
                notes_tidied += 1

        if setters:
            update_doc(task_id, setters)
            time.sleep(0.5)

    # ── STEP 3: Project notes for ANY tasks scheduled today ───────────────────
    today_tasks_all = get_today_tasks_all_for_projects()
    seen_parent_ids = set(
        t.get("parentId")
        for t in today_tasks_all
        if t.get("db") == "Tasks" and t.get("parentId") not in ("unassigned", "root", None)
    )

    if seen_parent_ids:
        print(f"\n📒  Aggressive: checking notes on {len(seen_parent_ids)} project(s) with tasks scheduled today (done + not done)...\n")
        for proj_id in seen_parent_ids:
            try:
                proj = get_doc(proj_id)
                proj_title = proj.get("title", proj_id)
                proj_note  = proj.get("note", "")

                if not proj_note or not proj_note.strip():
                    print(f"  —  No note on project: '{proj_title}'")
                    continue

                tidied = tidy_note(proj_note, proj_title)
                if tidied != proj_note:
                    now_ms = int(time.time() * 1000)
                    print(f"  📝  NOTE FIX   | Project: '{proj_title}'")
                    print(f"      BEFORE: {proj_note[:80]}{'...' if len(proj_note) > 80 else ''}")
                    print(f"      AFTER:  {tidied[:80]}{'...' if len(tidied) > 80 else ''}")
                    update_doc(proj_id, [
                        {"key": "note",              "val": tidied},
                        {"key": "fieldUpdates.note", "val": now_ms}
                    ])
                    notes_tidied += 1
                    time.sleep(0.5)
                else:
                    print(f"  ✔  Note OK     | Project: '{proj_title}'")

            except Exception as e:
                print(f"  ⚠️  Could not fetch project {proj_id}: {e}")

    # ── STEP 4: Inbox tasks — assign project + missing dates + notes ──────────
    print(f"\n📥  Fetching inbox tasks (no project assigned)...")
    inbox_tasks = get_inbox_tasks()
    print(f"📋  Found {len(inbox_tasks)} inbox task(s).\n")

    project_assigned = 0

    for task in inbox_tasks:
        task_id  = task.get("_id", "")
        title    = task.get("title", "Unknown task")
        day      = task.get("day")
        note     = task.get("note", "")
        setters  = []
        now_ms   = int(time.time() * 1000)

        # Assign project
        if admin_id:
            proj_id, proj_name = assign_project(title, projects, admin_id)
            print(f"  📁  PROJECT    | '{title}' → '{proj_name}'")
            setters += [
                {"key": "parentId",              "val": proj_id},
                {"key": "fieldUpdates.parentId", "val": now_ms}
            ]
            project_assigned += 1
        else:
            print(f"  ⚠️  UNSURE     | '{title}' — no Admin fallback, skipping")

        # Assign today's date if missing
        if not day:
            print(f"  📅  DATE FIX   | '{title}' → assigned {TODAY}")
            setters += [
                {"key": "day",              "val": TODAY},
                {"key": "fieldUpdates.day", "val": now_ms},
                {"key": "firstScheduled",   "val": TODAY}
            ]
            date_assigned += 1

        # Tidy inbox task note
        if note and note.strip():
            tidied = tidy_note(note, title)
            if tidied != note:
                print(f"  📝  NOTE FIX   | Inbox task: '{title}'")
                setters += [
                    {"key": "note",              "val": tidied},
                    {"key": "fieldUpdates.note", "val": now_ms}
                ]
                notes_tidied += 1

        if setters:
            update_doc(task_id, setters)
            time.sleep(0.5)

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    input_cost  = (usage_log["prompt_tokens"]     / 1_000_000) * COST_PER_1M_INPUT
    output_cost = (usage_log["completion_tokens"] / 1_000_000) * COST_PER_1M_OUTPUT
    total_cost  = input_cost + output_cost

    print(f"""
✅  All done!
   ✏️  Spelling fixes (tasks):          {spell_fixed}
   📅  Dates assigned:                  {date_assigned}
   📁  Projects assigned (inbox):       {project_assigned}
   📝  Notes tidied (tasks + projects): {notes_tidied}

💰  OpenAI Usage:
   📨  API calls made:      {usage_log['calls']}
   ➡️   Input tokens:        {usage_log['prompt_tokens']:,}
   ⬅️   Output tokens:       {usage_log['completion_tokens']:,}
   💵  Estimated cost:      ${total_cost:.6f} USD (~£{total_cost * 0.79:.6f})
""")


if __name__ == "__main__":
    main()

