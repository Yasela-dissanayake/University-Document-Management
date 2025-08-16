
from __future__ import annotations

import json
import os
import sys
import hashlib


from dotenv import load_dotenv

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

import time
from datetime import datetime, timezone

try:
    from dateutil import parser as dtparser  # optional, nicer ISO parsing if installed
except Exception:
    dtparser = None

# --- Make sure package imports work whether run as a module or a script -----
BASE_DIR = os.path.dirname(__file__)                  # .../python_backend
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


load_dotenv(os.path.join(REPO_ROOT, ".env"))
# --- Backend clients --------------------------------------------------------
# These are your existing classes. They read RPC / addresses from .env.
from python_backend.wallet_manager import get_or_create_wallet, get_address
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

def _normalize_store_result(res, doc: dict) -> tuple[str, str]:
    """
    Normalize whatever ipfs.store_academic_document returns into (ipfs_hash, content_hash).

    Accepts:
      - dict: uses common keys (ipfs_hash/cid/Hash/path, content_hash/sha256)
      - tuple/list: takes first two items (CID, hash)
      - str: treated as CID

    If content_hash is missing, compute SHA-256 over the canonicalized JSON doc.
    """
    ipfs_hash = ""
    content_hash = ""

    if isinstance(res, dict):
        ipfs_hash = (
            res.get("ipfs_hash")
            or res.get("cid")
            or res.get("Hash")
            or res.get("path")
            or ""
        )
        content_hash = res.get("content_hash") or res.get("sha256") or ""
    elif isinstance(res, (list, tuple)):
        if len(res) >= 1:
            ipfs_hash = str(res[0])
        if len(res) >= 2:
            content_hash = str(res[1])
    else:
        ipfs_hash = str(res)

    if not content_hash:
        canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
        content_hash = hashlib.sha256(canonical).hexdigest()

    return ipfs_hash, content_hash


# --- Routes -----------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("register"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name       = request.form.get("name", "").strip()
        program    = request.form.get("program", "").strip()
        year_raw   = request.form.get("year", "").strip()
        doc_raw    = request.form.get("document", "").strip()

        if not (student_id and name and program and year_raw):
            flash("Please fill Student ID, Name, Program, and Year.", "error")
            return redirect(url_for("register"))

        try:
            year = int(year_raw)
        except ValueError:
            flash("Year must be an integer.", "error")
            return redirect(url_for("register"))

        # 1) Create/fetch a custodial wallet behind the scenes
        try:
            student_wallet, created = get_or_create_wallet(student_id)
        except Exception as e:
            flash(f"Wallet creation failed: {e}", "error")
            return redirect(url_for("register"))

        # 2) Optionally store the first-semester transcript on IPFS
        ipfs_hash = ""
        content_hash = ""
        timestamp = int(datetime.utcnow().timestamp())
        try:
            if doc_raw:
                doc = json.loads(doc_raw)
                # keep UX simple but prevent mismatched IDs if present in JSON
                if doc.get("student_id") and doc["student_id"] != student_id:
                    flash("Document student_id does not match the form student_id.", "error")
                    return redirect(url_for("register"))

                if "timestamp" not in doc:
                    doc["timestamp"] = timestamp
                # ipfs_hash, content_hash = ipfs.store_academic_document(doc)  # returns (cid, hex_hash)
                res = ipfs.store_academic_document(doc)
                ipfs_hash, content_hash = _normalize_store_result(res, doc)
                timestamp = int(doc["timestamp"])
        except Exception as e:
            flash(f"Failed to store document on IPFS: {e}", "error")
            return redirect(url_for("register"))

        # 3) Call the contract (service wallet pays gas). Try with student_wallet first,
        #    fall back to older client signature if your blockchain_client lacks the param.
        try:
            tx_hash = None
            try:
                # Newer client signature that includes student_wallet
                tx_hash = bc.register_student_record(
                    student_id=student_id,
                    name=name,
                    program=program,
                    year=year,
                    documents_ipfs_hash=ipfs_hash,
                    content_hash=content_hash,
                    timestamp=timestamp,
                    student_wallet=student_wallet,
                )
            except TypeError:
                # Backward-compat: older client without student_wallet param
                tx_hash = bc.register_student_record(
                    student_id=student_id,
                    name=name,
                    program=program,
                    year=year,
                    documents_ipfs_hash=ipfs_hash,
                    content_hash=content_hash,
                    timestamp=timestamp,
                )

            created_msg = " (new wallet created)" if created else ""
            flash(f"Registered {student_id} with wallet {student_wallet}{created_msg}. Tx: {tx_hash}", "success")
            return redirect(url_for("view", student_id=student_id))
        except Exception as e:
            flash(f"Blockchain registration failed: {e}", "error")
            return redirect(url_for("register"))

    # GET → render form
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

            # ipfs_hash, content_hash = ipfs.store_academic_document(doc)
            res = ipfs.store_academic_document(doc)
            ipfs_hash, content_hash = _normalize_store_result(res, doc)
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
        