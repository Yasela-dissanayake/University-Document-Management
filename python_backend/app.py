# python_backend/app.py
from __future__ import annotations

import json
import os
import sys
import time
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import (
    Flask, jsonify, redirect, render_template, request,
    url_for, flash, session
)

# --- .env early load ---
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    def load_dotenv(*args, **kwargs):
        return None

BASE_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(REPO_ROOT, ".env"))

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --- Clients & RBAC & ACL ---
from python_backend.blockchain_client import UniversityBlockchainClient  # type: ignore
from python_backend.ipfs_client import UniversityIPFSClient  # type: ignore
from python_backend.wallet_manager import get_or_create_wallet  # type: ignore
from python_backend import rbac  # type: ignore
from python_backend import acl   # <--- NEW

# --- Optional AI agent loader ---
import importlib.util
def _load_agent_function() -> Optional[Any]:
    try:
        from python_backend.ai.agent import answer_question  # type: ignore
        print("[AI] Loaded agent via package import.")
        return answer_question
    except Exception as e:
        print(f"[AI] Package import failed: {e}")
    agent_path = os.path.join(BASE_DIR, "ai", "agent.py")
    if os.path.exists(agent_path):
        try:
            spec = importlib.util.spec_from_file_location("udoc_agent", agent_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore
                return getattr(mod, "answer_question", None)
        except Exception as e:
            print(f"[AI] Dynamic import failed: {e}")
    return None

_AGENT_FUNC = _load_agent_function()

def ask_agent(question: str, user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if callable(_AGENT_FUNC):
        try:
            out = _AGENT_FUNC(question, user_context=user)
        except TypeError:
            out = _AGENT_FUNC(question)
        if isinstance(out, dict) and "answer" in out:
            return out
        return {
            "answer": str(out),
            "trace": {"note": "Agent returned a raw string. No structured trace available."}
        }
    return {
        "answer": "AI functionality not yet implemented.",
        "trace": {"error": "No agent function available"}
    }


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")

@app.context_processor
def inject_current_year():
    return {"current_year": datetime.utcnow().year}

bc = UniversityBlockchainClient()
ipfs = UniversityIPFSClient()

def _parse_timestamp(value, default_to_now=True) -> int:
    if value is None or value == "":
        return int(time.time()) if default_to_now else 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass
    try:
        from dateutil import parser as dtparser  # type: ignore
        dt = dtparser.isoparse(str(value))
        return int(dt.timestamp())
    except Exception:
        pass
    return int(time.time()) if default_to_now else 0

def _normalize_store_result(res, doc: dict) -> tuple[str, str]:
    ipfs_hash = ""
    content_hash = ""
    if isinstance(res, dict):
        ipfs_hash = res.get("ipfs_hash") or res.get("cid") or res.get("Hash") or res.get("path") or ""
        content_hash = res.get("content_hash") or res.get("sha256") or ""
    elif isinstance(res, (list, tuple)):
        if len(res) >= 1: ipfs_hash = str(res[0])
        if len(res) >= 2: content_hash = str(res[1])
    else:
        ipfs_hash = str(res)
    if not content_hash:
        canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
        content_hash = hashlib.sha256(canonical).hexdigest()
    return ipfs_hash, content_hash

def _canon_sid(s: str) -> str:
    return (s or "").strip().upper()

# --- Auth views ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_url = request.form.get("next") or request.args.get("next") or url_for("index")
        user = rbac.authenticate(username, password)
        if not user:
            flash("Invalid username or password.", "error")
            return redirect(url_for("login", next=next_url))
        session["uid"] = user["id"]
        session["user"] = user["username"]
        session["role"] = user["role"]
        flash(f"Welcome, {user['username']}!", "success")
        return redirect(next_url)
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Signed out.", "info")
    return redirect(url_for("index"))

def _current_user() -> Optional[Dict]:
    uid = session.get("uid")
    if not uid:
        return None
    return rbac.get_user_by_id(int(uid))

# --- Routes ---
@app.route("/")
def index():
    return redirect(url_for("register"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        student_id = _canon_sid(request.form.get("student_id", ""))
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

        try:
            student_wallet, _created = get_or_create_wallet(student_id)
        except Exception as e:
            flash(f"Wallet creation failed: {e}", "error")
            return redirect(url_for("register"))

        ipfs_hash = ""
        content_hash = ""
        timestamp = int(time.time())

        try:
            if doc_raw:
                doc = json.loads(doc_raw)
                doc["student_id"] = student_id
                timestamp = _parse_timestamp(doc.get("timestamp"))
                doc["timestamp"] = timestamp

                # --- NEW: encrypt and store with default ACL principals ---
                principals = acl.default_principals(student_id)
                ipfs_hash, content_hash = acl.encrypt_and_store(doc, principals, ipfs)

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
            flash(f"Registered {student_id}. Tx: {tx_hash}", "success")
            return redirect(url_for("view", student_id=student_id))
        except Exception as e:
            flash(f"Blockchain registration failed: {e}", "error")
            return redirect(url_for("register"))

    return render_template("register.html")

@app.route("/update", methods=["GET", "POST"])
def update():
    if request.method == "POST":
        student_id = _canon_sid(request.form.get("student_id", ""))
        doc_raw    = request.form.get("document", "").strip()

        if not student_id:
            flash("Student ID is required.", "error")
            return redirect(url_for("update"))
        if not doc_raw:
            flash("Please generate the Document JSON.", "error")
            return redirect(url_for("update"))

        try:
            doc = json.loads(doc_raw)
            doc["student_id"] = student_id
            timestamp = _parse_timestamp(doc.get("timestamp"))
            doc["timestamp"] = timestamp

            # --- NEW: encrypt & store with default ACL principals ---
            principals = acl.default_principals(student_id)
            ipfs_hash, content_hash = acl.encrypt_and_store(doc, principals, ipfs)

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

@app.route("/view", methods=["GET", "POST"])
def view():
    student = None
    semesters = []
    ipfs_hash = ""
    student_id = ""
    if request.method == "POST":
        student_id = _canon_sid(request.form.get("student_id", ""))
    else:
        student_id = _canon_sid(request.args.get("student_id", ""))

    if student_id:
        try:
            student = bc.get_student_details(student_id)
            semesters = bc.get_all_semester_hashes(student_id) or []
            if not isinstance(semesters, list):
                semesters = []
            ipfs_hash = (student or {}).get("documents_ipfs_hash") or ""
        except Exception as e:
            flash(f"Fetch failed: {e}", "error")

    return render_template("view.html", student=student, semesters=semesters, ipfs_hash=ipfs_hash, student_id=student_id)

@app.route("/offchain", methods=["POST"])
def offchain():
    """
    RBAC + ACL gate:
      - must be logged in & allowed by RBAC
      - document is encrypted on IPFS; decrypt only if user is on its ACL
    """
    ipfs_hash = (request.form.get("ipfs_hash") or "").strip()
    target_student_id = _canon_sid(request.form.get("student_id") or "")
    if not ipfs_hash or not target_student_id:
        flash("Missing parameters for off-chain access.", "error")
        return redirect(url_for("view"))

    user = _current_user()
    if not user:
        return redirect(url_for("login", next=url_for("view", student_id=target_student_id)))

    if not rbac.can_view_offchain(user, target_student_id):
        flash("You are not authorized to view this off-chain document.", "error")
        return redirect(url_for("view", student_id=target_student_id))

    try:
        payload = ipfs.retrieve_academic_document(ipfs_hash)
        # If this is an encrypted payload, unwrap using ACL; otherwise show raw
        if isinstance(payload, dict) and payload.get("enc") == "fernet-v1":
            data = acl.decrypt_for_user(user, target_student_id, ipfs_hash, payload)
        else:
            data = payload if isinstance(payload, dict) else {"raw": payload}
        return render_template("offchain.html", data=data)
    except PermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("view", student_id=target_student_id))
    except Exception as e:
        flash(f"Decrypt failed: {e}", "error")
        return redirect(url_for("view", student_id=target_student_id))

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
        user = _current_user()                # <- get session user
        answer = ask_agent(question, user)    # <- pass user to wrapper
                
        if isinstance(answer, dict):
            print("AI Trace:\n", answer.get("trace"))
        else:
            print("AI response was not a dict:", answer)

        
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": f"Agent failed: {e}"}), 500

@app.route('/dashboard')
def dashboard():
    user = session.get("user")
    role = user.get("role") if user else None
    show_validator_link = rbac.is_validator_role(role)
    return render_template("dashboard.html", show_validator_link=show_validator_link)

@app.route('/validator')
def validator_dashboard():
    user = session.get("user")
    role = user.get("role")
    if not rbac.is_validator_role(role):
        return "Access Denied", 403
    return render_template("validator_dashboard.html", role=role)

# ==========================
# Admin Role Management Routes
# ==========================
@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "ADMIN":
        return redirect(url_for("index"))
    return render_template("admin.html")


@app.route("/admin/users", methods=["GET"])
def admin_get_users():
    from python_backend.rbac import get_all_users
    if session.get("role") != "ADMIN":
        return jsonify({"error": "Access denied"}), 403
    return jsonify(get_all_users())


@app.route("/admin/set-role", methods=["POST"])
def admin_set_role():
    from python_backend.rbac import set_user_role
    if session.get("role") != "ADMIN":
        return jsonify({"error": "Access denied"}), 403

    data = request.json
    username = data.get("username")
    new_role = data.get("role")
    return jsonify(set_user_role(username, new_role))


@app.route("/session-debug")
def session_debug():
    return {
        "user": session.get("user"),
        "role": session.get("role")
    }



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
