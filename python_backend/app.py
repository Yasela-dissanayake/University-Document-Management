# python_backend/app.py
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    flash,
)

# --- Make sure package imports work whether run as a module or a script -----
BASE_DIR = os.path.dirname(__file__)                  # .../python_backend
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --- Backend clients --------------------------------------------------------
# These are your existing classes. They read RPC / addresses from .env.
from python_backend.blockchain_client import UniversityBlockchainClient  # type: ignore
from python_backend.ipfs_client import UniversityIPFSClient  # type: ignore

# --- Try to load the AI agent in a robust way --------------------------------
# We support both package import and file-based dynamic import, then
# expose a stable callable `ask_agent(question: str) -> str`.
_AGENT_FUNC = None

def _load_agent_function() -> Optional[Any]:
    """
    Attempt to load `answer_question` from python_backend.ai.agent.
    Works in both 'module' and 'script' invocations.
    """
    # 1) Try normal package import first
    try:
        from python_backend.ai.agent import answer_question  # type: ignore
        print("[AI] Loaded agent via package import.")
        return answer_question
    except Exception as e:
        print(f"[AI] Package import failed: {e}")

    # 2) Try dynamic import by file path (when run as a script)
    import importlib.util
    agent_path = os.path.join(BASE_DIR, "ai", "agent.py")
    if os.path.exists(agent_path):
        try:
            spec = importlib.util.spec_from_file_location("udoc_agent", agent_path)
            if not spec or not spec.loader:
                print("[AI] Dynamic import spec not created.")
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            fn = getattr(mod, "answer_question", None)
            print(f"[AI] Loaded agent via dynamic import: {type(fn)}")
            return fn
        except Exception as e:
            print(f"[AI] Dynamic import failed: {e}")
            return None
    else:
        print("[AI] agent.py not found at", agent_path)
        return None

_AGENT_FUNC = _load_agent_function()

def ask_agent(question: str) -> str:
    """
    Stable callable wrapper. If the real agent function exists, call it.
    Otherwise return a friendly fallback string.
    """
    if callable(_AGENT_FUNC):
        out = _AGENT_FUNC(question)
        # Some implementations return dicts like {"answer": "..."}
        if isinstance(out, dict) and "answer" in out:
            return str(out["answer"])
        return str(out)
    return "AI functionality not yet implemented."

# --- Flask app --------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

# Inject current year everywhere (avoid relying on unavailable Jinja filters)
@app.context_processor
def inject_current_year():
    return {"current_year": datetime.utcnow().year}

# Create singletons for backends
bc = UniversityBlockchainClient()
ipfs = UniversityIPFSClient()

# --- Helpers ----------------------------------------------------------------
def _parse_document_json(raw: str) -> Dict[str, Any]:
    """
    Normalize a JSON string from a textarea into a dict and ensure it has a timestamp.
    """
    data = json.loads(raw)
    if "timestamp" not in data:
        data["timestamp"] = int(datetime.utcnow().timestamp())
    return data

# --- Routes -----------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("register"))

# Register student (optional first-sem JSON)
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        program = request.form.get("program", "").strip()
        year = request.form.get("year", "").strip()
        doc_raw = request.form.get("document", "").strip()

        if not (student_id and name and program and year):
            flash("Please fill Student ID, Name, Program, and Year.", "error")
            return redirect(url_for("register"))

        try:
            year_int = int(year)
        except ValueError:
            flash("Year must be an integer.", "error")
            return redirect(url_for("register"))

        # Optionally store initial semester doc
        ipfs_hash = None
        content_hash = None
        timestamp = int(datetime.utcnow().timestamp())

        try:
            if doc_raw:
                doc = _parse_document_json(doc_raw)
                # Optionally enforce matching ID in document
                if doc.get("student_id") and doc["student_id"] != student_id:
                    flash("Document student_id does not match form student_id.", "error")
                    return redirect(url_for("register"))

                ipfs_hash, content_hash = ipfs.store_academic_document(doc)  # existing method in your repo
                timestamp = int(doc.get("timestamp", timestamp))

            tx = bc.register_student_record(
                student_id=student_id,
                name=name,
                program=program,
                year=year_int,
                documents_ipfs_hash=ipfs_hash or "",
                content_hash=content_hash or "",
                timestamp=timestamp,
            )
            flash(f"Registered {student_id}. Tx: {tx}", "success")
            return redirect(url_for("view"))
        except Exception as e:
            flash(f"Registration failed: {e}", "error")
            return redirect(url_for("register"))

    return render_template("register.html")

# Append a new semester (version)
@app.route("/update", methods=["GET", "POST"])
def update():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        doc_raw = request.form.get("document", "").strip()

        if not student_id:
            flash("Student ID is required.", "error")
            return redirect(url_for("update"))
        if not doc_raw:
            flash("Please paste a semester JSON document.", "error")
            return redirect(url_for("update"))

        try:
            doc = _parse_document_json(doc_raw)
            if doc.get("student_id") and doc["student_id"] != student_id:
                flash("Document student_id does not match form student_id.", "error")
                return redirect(url_for("update"))

            ipfs_hash, content_hash = ipfs.store_academic_document(doc)
            timestamp = int(doc.get("timestamp", int(datetime.utcnow().timestamp())))

            tx = bc.add_semester_record(
                student_id=student_id,
                documents_ipfs_hash=ipfs_hash,
                content_hash=content_hash,
                timestamp=timestamp,
            )
            flash(f"Added semester for {student_id}. Tx: {tx}", "success")
            return redirect(url_for("view", student_id=student_id))
        except Exception as e:
            flash(f"Update failed: {e}", "error")
            return redirect(url_for("update"))

    return render_template("update.html")

# Inspect a student's on-chain record & list all semesters
@app.route("/view", methods=["GET", "POST"])
def view():
    student = None
    semesters = []
    student_id = ""

    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
    else:
        # allow /view?student_id=S12345
        student_id = request.args.get("student_id", "").strip()

    if student_id:
        try:
            student = bc.get_student_details(student_id)
            semesters = bc.get_all_semester_hashes(student_id) or []
            if not isinstance(semesters, list):
                semesters = []
        except Exception as e:
            flash(f"Fetch failed: {e}", "error")

    return render_template("view.html", student=student, semesters=semesters)

# Decrypt & show a single off-chain document by IPFS hash
@app.route("/offchain", methods=["POST"])
def offchain():
    ipfs_hash = request.form.get("ipfs_hash", "").strip()
    if not ipfs_hash:
        flash("Missing IPFS hash.", "error")
        return redirect(url_for("view"))

    try:
        data = ipfs.retrieve_academic_document(ipfs_hash)
        # Ensure it's serializable
        if not isinstance(data, dict):
            data = {"raw": data}
        return render_template("offchain.html", data=data)
    except Exception as e:
        flash(f"Decrypt failed: {e}", "error")
        return redirect(url_for("view"))

# AI: page & API
@app.route("/ai")
def ai_page():
    return render_template("ai.html")

@app.route("/api/ai_query", methods=["POST"])
def ai_query():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return jsonify({"error": "Missing 'question'"}), 400
    try:
        answer = ask_agent(question)
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": f"Agent failed: {e}"}), 500

# Entry point
if __name__ == "__main__":
    # Running as a script is fine thanks to the sys.path fix above
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
