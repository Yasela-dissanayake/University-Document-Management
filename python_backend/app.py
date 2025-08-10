from datetime import datetime
import json
from flask import Flask, request, render_template, redirect, url_for, flash, jsonify

from blockchain_client import UniversityBlockchainClient
from ipfs_client import UniversityIPFSClient


app = Flask(__name__)
app.secret_key = 'supersecret'  # For flash messages

# Inject current year into all templates to avoid using unavailable Jinja filters.
@app.context_processor
def inject_current_year():
    """Provide the current UTC year to templates as `current_year`.

    Using a context processor avoids reliance on unavailable Jinja filters such
    as `date`, which may not be registered by default.  Templates can now
    display the year with `{{ current_year }}` safely.
    """
    return {'current_year': datetime.utcnow().year}

# Initialise clients once at startup.  In a real application you may want
# to handle reconnection logic or lazy loading but for this demo it's
# sufficient to create singletons here.
blockchain_client = UniversityBlockchainClient()
ipfs_client = UniversityIPFSClient()


@app.route('/')
def index():
    """Simple landing page showing navigation."""
    return render_template("layout.html")


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Register a new student with an initial semester document.

    The form accepts student_id, name, program, year and an optional
    JSON document for the first semester.  If no JSON document is
    provided, a minimal dummy transcript is generated automatically.
    """
    if request.method == 'POST':
        s_id = request.form.get('student_id', '').strip()
        name = request.form.get('name', '').strip()
        program = request.form.get('program', '').strip()
        year = request.form.get('year', '').strip()
        doc_json = request.form.get('document', '').strip()

        # Basic validation
        if not (s_id and name and program and year):
            flash("All fields are required.")
            return render_template("register.html")
        try:
            year_int = int(year)
        except ValueError:
            flash("Year must be a number.")
            return render_template("register.html")

        # Parse provided JSON or create default document
        if doc_json:
            try:
                doc = json.loads(doc_json)
            except json.JSONDecodeError as e:
                flash(f"Invalid document JSON: {e}")
                return render_template("register.html")
        else:
            doc = {
                "student_id": s_id,
                "document_type": "transcript",
                "timestamp": datetime.utcnow().isoformat() + 'Z',
                "courses": [
                    {"code": "CS101", "name": "Intro to CS", "grade": "A"}
                ],
                "gpa": 4.0,
            }

        # Ensure the student ID matches the document
        doc['student_id'] = s_id

        # Prepare student metadata
        student_data = {
            "student_id": s_id,
            "name": name,
            "program": program,
            "year": year_int,
        }

        try:
            # Store document on IPFS
            offchain = ipfs_client.store_academic_document(doc)
            # Register student on the blockchain
            blockchain_client.register_student_record(
                student_data, offchain['ipfs_hash'], offchain['content_hash']
            )
            flash(f"Registered student {s_id}. IPFS: {offchain['ipfs_hash']}")
            return redirect(url_for('register'))
        except Exception as e:
            flash(f"Error: {e}")
    return render_template("register.html")


@app.route('/update', methods=['GET', 'POST'])
def update():
    """Append a new semester record to an existing student.

    This form accepts a student ID and a JSON document representing the
    transcript.  The document must be valid JSON.  The student must
    already be registered on chain.
    """
    if request.method == 'POST':
        s_id = request.form.get('student_id', '').strip()
        doc_json = request.form.get('document', '').strip()

        if not s_id:
            flash("Student ID is required.")
            return render_template("update.html")
        if not doc_json:
            flash("Document JSON is required.")
            return render_template("update.html")
        try:
            doc = json.loads(doc_json)
        except json.JSONDecodeError as e:
            flash(f"Invalid document JSON: {e}")
            return render_template("update.html")
        # Ensure the document uses the correct student ID
        doc['student_id'] = s_id
        try:
            offchain = ipfs_client.store_academic_document(doc)
            blockchain_client.add_semester_record(
                s_id, offchain['ipfs_hash'], offchain['content_hash']
            )
            flash(f"Appended semester for {s_id}. IPFS: {offchain['ipfs_hash']}")
            return redirect(url_for('update'))
        except Exception as e:
            flash(f"Error: {e}")
    return render_template("update.html")


@app.route('/view', methods=['GET', 'POST'])
def view():
    """View a student's on‑chain details and list all semester documents.

    Submitting the form with a student ID fetches both the core student
    metadata (latest document pointer, name, program, etc.) and the full
    list of semester IPFS hashes.  These hashes are displayed with
    buttons to decrypt and view each corresponding document.
    """
    student = None
    latest_ipfs = None
    semesters = None
    if request.method == 'POST':
        s_id = request.form.get('student_id', '').strip()
        if s_id:
            student = blockchain_client.get_student_details(s_id)
            if student:
                latest_ipfs = student['documents_ipfs_hash']
                semesters = blockchain_client.get_all_semester_hashes(s_id)
    return render_template(
        "view.html", student=student, ipfs_hash=latest_ipfs, semesters=semesters
    )


@app.route('/offchain', methods=['POST'])
def offchain():
    """Decrypt and display a document given its IPFS hash."""
    ipfs_hash = request.form.get('ipfs_hash', '').strip()
    if not ipfs_hash:
        return "Missing IPFS hash.", 400
    try:
        decrypted = ipfs_client.retrieve_academic_document(ipfs_hash)
        return render_template("offchain.html", data=decrypted)
    except Exception as e:
        return f"Could not retrieve/decrypt: {e}", 500


@app.route('/api/ai_query', methods=['POST'])
def ai_query():
    """API endpoint for AI query (placeholder)."""
    question = request.json.get('question', '')
    if not question:
        return jsonify({"error": "No question provided."}), 400
    # Placeholder: integrate with LangGraph agent here
    return jsonify({"answer": "AI functionality not yet implemented."})

# Display a page to ask questions to the AI agent
@app.route('/ai')
def ai_page():
    """Render the AI query page.

    The template contains a simple form and a script that POSTs the question
    to the `/api/ai_query` endpoint and displays the returned answer.  This
    separates the UI from the API logic.
    """
    return render_template('ai.html')


if __name__ == '__main__':
    app.run(debug=True)