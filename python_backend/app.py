from flask import Flask, request, render_template, redirect, url_for, flash
from blockchain_client import UniversityBlockchainClient
from ipfs_client import UniversityIPFSClient

app = Flask(__name__)
app.secret_key = 'supersecret'  # For flash messages

blockchain_client = UniversityBlockchainClient()
ipfs_client = UniversityIPFSClient()

@app.route('/')
def index():
    return render_template("layout.html")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        s_id = request.form['student_id']
        name = request.form['name']
        program = request.form['program']
        year = int(request.form['year'])

        # --- For demo, auto-create dummy transcript ---
        doc = {
            "student_id": s_id, "document_type": "transcript",
            "timestamp": "2025-08-01T12:00:00Z",
            "courses": [{"code": "CS101", "name": "Intro to CS", "grade": "A"}],
            "gpa": 4.0
        }
        offchain = ipfs_client.store_academic_document(doc)
        student = {
            "student_id": s_id, "name": name, "program": program, "year": year
        }
        try:
            blockchain_client.register_student_record(student, offchain['ipfs_hash'], offchain['content_hash'])
            flash(f"Registered student {s_id}. IPFS: {offchain['ipfs_hash']}")
            return redirect(url_for('register'))
        except Exception as e:
            flash(f"Error: {e}")
    return render_template("register.html")

@app.route('/view', methods=['GET', 'POST'])
def view():
    student = None; ipfs_hash = None; offchain_data = None
    if request.method == 'POST':
        student_id = request.form['student_id']
        student = blockchain_client.get_student_details(student_id)
        if student:
            ipfs_hash = student['documents_ipfs_hash']
    return render_template("view.html", student=student, ipfs_hash=ipfs_hash)

@app.route('/offchain', methods=['POST'])
def offchain():
    ipfs_hash = request.form['ipfs_hash']
    try:
        decrypted = ipfs_client.retrieve_academic_document(ipfs_hash)
        return render_template("offchain.html", data=decrypted)
    except Exception as e:
        return f"Could not retrieve/decrypt: {e}"

# FORWARD-COMPATIBILITY: AI endpoint placeholder
@app.route('/ai', methods=['GET', 'POST'])
def ai():
    answer = ''
    if request.method == 'POST':
        query = request.form['query']
        answer = "AI features coming soon!"  # Plug your AI agent later
    return render_template("ai.html", answer=answer)

if __name__ == '__main__':
    app.run(debug=True)
