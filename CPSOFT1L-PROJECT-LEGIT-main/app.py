from datetime import datetime, timezone, timedelta
import csv
import io
import hashlib
import os
import random
import string
import json
import smtplib
from email.message import EmailMessage
from contextlib import closing
from werkzeug.utils import secure_filename
from urllib.parse import urlparse
from uuid import uuid4
from flask import Flask, flash, redirect, render_template, request, session, url_for, Response
from dotenv import load_dotenv
from db_models import get_connection, hash_password, init_db, UserRegisterSchema

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "nu_study_secret_key_2026"

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.csv', '.png', '.jpg', '.jpeg', '.zip'}

def _save_upload(file):
    original = secure_filename(file.filename or '')
    if not original or os.path.splitext(original)[1].suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError('Choose a supported file (PDF, Office, image, text, or ZIP; maximum 20 MB).')
    name = uuid4().hex + '_' + original
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], name))
    return name

def _valid_external_url(value):
    parsed = urlparse(value)
    return parsed.scheme in ('https', 'http') and bool(parsed.netloc)


init_db()

def _module_access(cursor, module, student_id):
    """All enrolled students may browse and complete every week independently."""
    return True, ''


def _parse_db_timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=timezone.utc) if '+' not in str(value) and 'Z' not in str(value) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def _course_teacher(cursor, course_id):
    cursor.execute('SELECT * FROM courses WHERE id = ? AND instructor_id = ?', (course_id, session.get('user_id')))
    return cursor.fetchone()

def _welcome_email(email, name):
    """Optional real email: configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM."""
    host = os.getenv('SMTP_HOST')
    sender = os.getenv('SMTP_FROM')
    if not host or not sender:
        return False
    message = EmailMessage()
    message['From'] = sender
    message['To'] = email
    message['Subject'] = 'Welcome to NU Study'
    message.set_content(f'Welcome back, {name}!\n\nYou have successfully signed in to NU Study.')
    try:
        with smtplib.SMTP(host, int(os.getenv('SMTP_PORT', '587')), timeout=5) as smtp:
            smtp.starttls()
            if os.getenv('SMTP_USER'):
                smtp.login(os.getenv('SMTP_USER'), os.getenv('SMTP_PASSWORD', ''))
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.exception('Could not send welcome email')
        return False


def _grade_rows(cur, course_id, student_id):
    cur.execute("""SELECT m.id, m.title, m.type, m.due_date, s.id AS submission_id,
      s.score, s.total_questions, s.manual_grade, s.feedback, s.submitted_at
      FROM modules m LEFT JOIN student_submissions s ON s.module_id=m.id AND s.student_id=?
      WHERE m.course_id=? AND UPPER(m.type) IN ('QUIZ','EXAM','ASSIGNMENT')
      ORDER BY m.id""", (student_id, course_id))
    rows=[]
    for r in cur.fetchall():
        item=dict(r)
        pct=None
        manual=item['manual_grade']
        if manual is not None and str(manual).strip():
            try: pct=float(manual)
            except (ValueError, TypeError): pct=None
        elif item['submission_id'] is not None and item['type'].upper() in ('QUIZ','EXAM') and item['total_questions']:
            pct=100*item['score']/item['total_questions']
        item['percentage']=round(pct,2) if pct is not None else None
        rows.append(item)
    return rows


def _grade_summary(rows):
    kinds=('QUIZ','ASSIGNMENT','EXAM')
    categories={}
    for kind in kinds:
        scored=[x['percentage'] for x in rows if x['type'].upper()==kind and x['percentage'] is not None]
        categories[kind]={'average':round(sum(scored)/len(scored),2) if scored else None,'graded':len(scored),'total':sum(x['type'].upper()==kind for x in rows)}
    scored=[x['percentage'] for x in rows if x['percentage'] is not None]
    return {'overall':round(sum(scored)/len(scored),2) if scored else None,'categories':categories,'graded':len(scored),'total':len(rows)}


def _grade_access(cur, course_id):
    cur.execute('SELECT * FROM courses WHERE id = ?', (course_id,))
    course=cur.fetchone()
    if not course: return None
    if session.get('role') == 'TEACHER' and course['instructor_id'] == session.get('user_id'): return course
    if session.get('role') == 'STUDENT':
        cur.execute('SELECT 1 FROM enrollments WHERE course_id=? AND student_id=?',(course_id,session['user_id']))
        if cur.fetchone(): return course
    return None

@app.route("/")
def index():
    if "user_id" in session: return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login.html", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("usernameInput", "").strip()
        password = request.form.get("passwordInput", "")
        with closing(get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            user = cursor.fetchone()
            if user and hash_password(password, user["salt"])[0] == user["password_hash"]:
                session["user_id"] = user["id"]
                session["role"] = user["role"]
                session['welcome_name'] = user['firstname']
                cursor.execute('INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)',
                    (user['id'], 'Welcome back!', f"Welcome to NU Study, {user['firstname']}!"))
                conn.commit()
                _welcome_email(user['email'], user['firstname'])
                return redirect(url_for("dashboard"))
        flash("Invalid email or password", "danger")
    return render_template("login.html")

@app.route("/register.html", methods=["GET", "POST"])
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            validated = UserRegisterSchema(
                firstname=request.form.get("firstname"),
                lastname=request.form.get("lastname"),
                email=request.form.get("email"),
                password=request.form.get("password"),
                role=request.form.get("role", "STUDENT").strip().upper(),
                gender=request.form.get("gender")
            )
            password_hash, salt = hash_password(validated.password)
            with closing(get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO users (firstname, lastname, email, password_hash, salt, role, gender) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                               (validated.firstname, validated.lastname, validated.email, password_hash, salt, validated.role, validated.gender))
                conn.commit()
            flash("Account registered! Please log in.", "success")
            return redirect(url_for("login"))
        except Exception as e:
            message = str(e).lower()
            if 'password' in message:
                flash('Password must be at least 8 characters and include uppercase, lowercase, a number, and a special character.', 'danger')
            elif 'email' in message or 'unique' in message:
                flash('Please use a valid email address that is not already registered.', 'danger')
            else:
                flash('Unable to register. Please check your information and try again.', 'danger')
    return render_template("register.html")

@app.route("/dashboard.html")
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session: return redirect(url_for("login"))
    user_id = session["user_id"]
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        
        if not current_user:
            session.clear()
            return redirect(url_for("login"))

        if current_user["role"] == "STUDENT":
            cursor.execute("SELECT c.* FROM courses c JOIN enrollments e ON c.id = e.course_id WHERE e.student_id = ?", (user_id,))
            courses_list = cursor.fetchall()
            cursor.execute("""
                SELECT m.*, c.code as course_code FROM modules m
                JOIN courses c ON m.course_id = c.id
                JOIN enrollments e ON c.id = e.course_id
                WHERE e.student_id = ? 
                AND m.due_date IS NOT NULL AND m.due_date != ''
                AND m.id NOT IN (SELECT module_id FROM student_progress WHERE student_id = ?)
                ORDER BY m.due_date ASC LIMIT 5
            """, (user_id, user_id))
            deadlines = cursor.fetchall()
        else:
            cursor.execute("SELECT * FROM courses WHERE instructor_id = ?", (user_id,))
            courses_list = cursor.fetchall()
            deadlines = []
            
    return render_template("dashboard.html", user=current_user, courses=courses_list, deadlines=deadlines)

@app.route("/courses.html")
@app.route("/courses")
def courses():
    if "user_id" not in session: return redirect(url_for("login"))
    user_id = session["user_id"]
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        
        if current_user["role"] == "STUDENT":
            cursor.execute("SELECT c.* FROM courses c JOIN enrollments e ON c.id = e.course_id WHERE e.student_id = ?", (user_id,))
            raw_courses = cursor.fetchall()
            courses_list = []
            for c in raw_courses:
                course_dict = dict(c)
                cursor.execute("SELECT COUNT(*) as total FROM modules WHERE course_id = ?", (c["id"],))
                total_modules = cursor.fetchone()["total"]
                cursor.execute("""
                    SELECT COUNT(*) as done FROM student_progress sp
                    JOIN modules m ON sp.module_id = m.id
                    WHERE sp.student_id = ? AND m.course_id = ?
                """, (user_id, c["id"]))
                completed_modules = cursor.fetchone()["done"]
                course_dict["progress"] = int((completed_modules / total_modules) * 100) if total_modules > 0 else 0
                courses_list.append(course_dict)
        else:
            cursor.execute("SELECT * FROM courses WHERE instructor_id = ?", (user_id,))
            courses_list = [dict(row) for row in cursor.fetchall()]
        
    return render_template("courses.html", courses=courses_list, user=current_user)

@app.route("/course_detail.html")
@app.route("/course_detail")
def course_detail():
    if "user_id" not in session: return redirect(url_for("login"))
    course_id = request.args.get("id", 1)
    user_id = session["user_id"]
    
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        
        cursor.execute("SELECT * FROM courses WHERE id = ?", (course_id,))
        course = cursor.fetchone()
        if not course: return 'Course not found', 404
        if current_user['role'] == 'STUDENT':
            cursor.execute('SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?', (user_id, course_id))
            if not cursor.fetchone(): return 'Not enrolled', 403
        elif course['instructor_id'] != user_id: return 'Unauthorized', 403
        cursor.execute("SELECT * FROM modules WHERE course_id = ? ORDER BY section_name, id", (course_id,))
        modules_raw = [dict(row) for row in cursor.fetchall()]
        
        completed_ids = set()
        if current_user["role"] == "STUDENT":
            cursor.execute("SELECT module_id FROM student_progress WHERE student_id = ?", (user_id,))
            completed_ids = {row["module_id"] for row in cursor.fetchall()}

        sections = {}
        for m in modules_raw:
            m_dict = dict(m)
            m_dict["is_completed"] = m_dict["id"] in completed_ids
            if current_user['role'] == 'STUDENT':
                allowed, reason = _module_access(cursor, m_dict, user_id)
                m_dict['locked'] = not allowed
                m_dict['lock_reason'] = reason
            sec = m_dict['section_name'] or "General Resources"
            if sec not in sections:
                sections[sec] = []
            sections[sec].append(m_dict)

    ordered_sections = {f'Week {i}': sections.pop(f'Week {i}', []) for i in range(1, 14)}
    ordered_sections.update(sections)
    return render_template("course_detail.html", course=course, sections=ordered_sections, user=current_user)

@app.route("/student/join_course", methods=["POST"])
def join_course():
    if session.get("role") != "STUDENT": return "Unauthorized", 403
    enrollment_code = request.form.get("enrollment_code", "").strip().upper()
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM courses WHERE enrollment_code = ?", (enrollment_code,))
        course = cursor.fetchone()
        if not course:
            flash("Invalid enrollment code.", "danger")
        else:
            try:
                cursor.execute("INSERT INTO enrollments (student_id, course_id) VALUES (?, ?)", (session["user_id"], course["id"]))
                conn.commit()
                flash("Successfully enrolled!", "success")
            except Exception:
                flash("Already enrolled in this course.", "warning")
    return redirect(url_for("courses"))

@app.route("/teacher/create_course", methods=["POST"])
def create_course():
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    code = request.form.get("code")
    title = request.form.get("title")
    description = request.form.get("description")
    enrollment_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO courses (code, title, description, instructor_id, enrollment_code)
                VALUES (?, ?, ?, ?, ?)
            """, (code, title, description, session["user_id"], enrollment_code))
            conn.commit()
            flash("Course created successfully!", "success")
        except Exception as e:
            flash(f"Error creating course: {str(e)}", "danger")
    return redirect(url_for("courses"))

def _announce_module(cursor, course_id, title, kind, due_date, action="uploaded"):
    """Create student notifications for newly published or updated content."""
    cursor.execute("SELECT code FROM courses WHERE id = ?", (course_id,))
    course = cursor.fetchone()
    code = course['code'] if course else 'Your course'
    label = {'QUIZ':'quiz', 'EXAM':'exam', 'ASSIGNMENT':'assignment', 'LECTURE':'lecture'}.get((kind or '').upper(), (kind or 'module').lower())
    date_text = (' Due on ' + str(due_date).replace('T', ' ') + '.') if due_date else ' No due date set.'
    cursor.execute("SELECT student_id FROM enrollments WHERE course_id = ?", (course_id,))
    for student in cursor.fetchall():
        cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)",
            (student['student_id'], f'Your teacher {action} a {label}', f'[{code}] {title}.{date_text}'))


@app.route("/teacher/create_timed_quiz", methods=["POST"])
def create_timed_quiz():
    """Create a timed assessment and its first question from one instructor form."""
    if session.get("role") != "TEACHER":
        return "Unauthorized", 403
    try:
        course_id = int(request.form.get("course_id", ""))
        minutes = int(request.form.get("time_limit_minutes", ""))
        pass_pct = int(request.form.get("pass_percentage", "70"))
    except (ValueError, TypeError):
        return "Invalid course or quiz settings", 400
    title = (request.form.get("title") or "").strip()
    section = (request.form.get("section_name") or "Assessments").strip()
    question = (request.form.get("question_text") or "").strip()
    qtype, options, correct = _question_form_values(request.form)
    kind = (request.form.get("type") or "QUIZ").upper()
    due = request.form.get("due_date") or None
    if not (title and section and question and _question_valid(qtype, options, correct)
            and kind in ("QUIZ", "EXAM") and 1 <= minutes <= 1440 and 0 <= pass_pct <= 100):
        return "Invalid assessment settings or first question", 400
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        owner = _course_teacher(cursor, course_id)
        if not owner:
            return "You do not own this course", 403
        if owner['records_locked']:
            return "Course records are locked", 403
        cursor.execute("""INSERT INTO modules (course_id, section_name, title, type, content, due_date,
            time_limit_minutes, pass_percentage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (course_id, section, title, kind, request.form.get("content", ""), due, minutes, pass_pct))
        module_id = cursor.lastrowid
        cursor.execute("""INSERT INTO quiz_questions (module_id, question_text, option_a, option_b,
            option_c, option_d, correct_answer, question_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (module_id, question, *options, correct, qtype))
        _announce_module(cursor, course_id, title, kind, due)
        conn.commit()
    flash("Timed assessment created! You can add more questions below.", "success")
    return redirect(url_for("module_view", id=module_id))

@app.route("/teacher/add_module", methods=["POST"])
def add_module():
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    course_id = request.form.get("course_id")
    section_name = request.form.get("section_name", "General Resources")
    title = request.form.get("title")
    m_type = request.form.get("type")
    content = request.form.get("content", "")
    due_date = request.form.get("due_date", None)
    time_limit = max(0, min(1440, int(request.form.get('time_limit_minutes') or 0)))
    pass_percentage = max(0, min(100, int(request.form.get('pass_percentage') or 70)))
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO modules (course_id, section_name, title, type, content, due_date, time_limit_minutes, pass_percentage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (course_id, section_name, title, m_type, content, due_date, time_limit, pass_percentage))
        _announce_module(cursor, course_id, title, m_type, due_date)
        conn.commit()

    flash("Module added successfully!", "success")
    return redirect(url_for("course_detail", id=course_id))

@app.route("/teacher/edit_module", methods=["POST"])
def edit_module():
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    module_id = request.form.get("module_id")
    title = request.form.get("title")
    m_type = request.form.get("type")
    section_name = request.form.get("section_name")
    content = request.form.get("content")
    due_date = request.form.get("due_date")
    time_limit = max(0, min(1440, int(request.form.get('time_limit_minutes') or 0)))
    pass_percentage = max(0, min(100, int(request.form.get('pass_percentage') or 70)))

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT course_id FROM modules WHERE id = ?", (module_id,))
        mod = cursor.fetchone()
        course_id = mod["course_id"] if mod else 1

        cursor.execute("""
            UPDATE modules SET title = ?, type = ?, section_name = ?, content = ?, due_date = ?, time_limit_minutes = ?, pass_percentage = ? WHERE id = ?
        """, (title, m_type, section_name, content, due_date, time_limit, pass_percentage, module_id))
        _announce_module(cursor, course_id, title, m_type, due_date, action="updated")
        conn.commit()

    flash("Module updated successfully!", "success")
    return redirect(url_for("module_view", id=module_id))

@app.route("/teacher/delete_module/<int:module_id>", methods=["POST"])
def delete_module(module_id):
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT course_id FROM modules WHERE id = ?", (module_id,))
        mod = cursor.fetchone()
        course_id = mod["course_id"] if mod else 1
        
        cursor.execute("DELETE FROM modules WHERE id = ?", (module_id,))
        conn.commit()

    flash("Module deleted successfully.", "success")
    return redirect(url_for("course_detail", id=course_id))

def _grade_assessment(cursor, student_id, module_id, answers):
    # Grade answered items; blanks count as wrong.
    cursor.execute('SELECT * FROM quiz_questions WHERE module_id = ?', (module_id,))
    questions = cursor.fetchall()
    score = sum(1 for q in questions if str(answers.get(str(q['id'])) or '').strip()
                and ' '.join(str(answers.get(str(q['id']))).split()).casefold() ==
                ' '.join(str(q['correct_answer']).split()).casefold())
    cursor.execute('''INSERT INTO student_submissions (student_id, module_id, score, total_questions, answers_json)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT(student_id, module_id) DO NOTHING''',
        (student_id, module_id, score, len(questions), json.dumps(answers)))
    cursor.execute('''INSERT INTO student_progress (student_id, module_id) VALUES (?, ?)
        ON CONFLICT(student_id, module_id) DO NOTHING''', (student_id, module_id))
    return score, len(questions)


@app.route('/student/autosave_assessment', methods=['POST'])
def autosave_assessment():
    if session.get('role') != 'STUDENT': return 'Unauthorized', 403
    data = request.get_json(silent=True) or {}
    module_id = data.get('module_id')
    answers = data.get('answers')
    if not isinstance(answers, dict): return 'Invalid answers', 400
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM modules WHERE id = ?', (module_id,))
        mod = cur.fetchone()
        if not mod or (mod['type'] or '').upper() not in ('QUIZ', 'EXAM'): return 'Invalid assessment', 400
        cur.execute('SELECT 1 FROM enrollments WHERE student_id=? AND course_id=?', (session['user_id'], mod['course_id']))
        if not cur.fetchone(): return 'Not enrolled', 403
        cur.execute('SELECT 1 FROM student_submissions WHERE student_id=? AND module_id=?', (session['user_id'], module_id))
        if cur.fetchone(): return 'Already submitted', 409
        cur.execute('SELECT * FROM assessment_attempts WHERE student_id=? AND module_id=?', (session['user_id'], module_id))
        attempt = cur.fetchone()
        if not attempt: return 'Assessment not started', 403
        if mod['time_limit_minutes']:
            deadline = _parse_db_timestamp(attempt['started_at']) + timedelta(minutes=mod['time_limit_minutes'])
            if datetime.now(timezone.utc) >= deadline:
                stored = json.loads(attempt['draft_answers_json'] or '{}')
                _grade_assessment(cur, session['user_id'], module_id, stored)
                conn.commit()
                return {'expired': True}, 409
        cur.execute('UPDATE assessment_attempts SET draft_answers_json=? WHERE student_id=? AND module_id=?',
                    (json.dumps({str(k): str(v)[:500] for k,v in answers.items() if v is not None}), session['user_id'], module_id))
        conn.commit()
    return {'saved': True}


@app.route("/module_view")
def module_view():
    if "user_id" not in session: return redirect(url_for("login"))
    module_id = request.args.get("id")
    user_id = session["user_id"]
    
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        
        cursor.execute("SELECT * FROM modules WHERE id = ?", (module_id,))
        module = cursor.fetchone()
        
        if not module: return 'Module not found', 404
        if current_user['role'] == 'STUDENT':
            cursor.execute('SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?', (user_id, module['course_id']))
            if not cursor.fetchone(): return 'Not enrolled', 403
            allowed, reason = _module_access(cursor, module, user_id)
            if not allowed:
                flash(reason, 'warning')
                return redirect(url_for('course_detail', id=module['course_id']))
        elif current_user['role'] == 'TEACHER':
            if not _course_teacher(cursor, module['course_id']): return 'Unauthorized', 403
        cursor.execute("SELECT * FROM courses WHERE id = ?", (module["course_id"],))
        course = cursor.fetchone()

        cursor.execute("SELECT * FROM quiz_questions WHERE module_id = ?", (module_id,))
        questions = cursor.fetchall()

        cursor.execute("SELECT * FROM content_blocks WHERE module_id = ?", (module_id,))
        content_blocks = cursor.fetchall()

        # Prevent students from viewing empty modules until content blocks or questions are added
        m_type = (module["type"] or "").upper()
        if current_user["role"] == "STUDENT":
            if m_type in ["QUIZ", "EXAM"] and not questions:
                flash("This assessment is still being prepared by your instructor (no questions added yet).", "warning")
                return redirect(url_for('course_detail', id=module["course_id"]))
            
            if m_type in ["LESSON", "LECTURE"] and not content_blocks:
                flash("This material is still being prepared by your instructor (no content blocks added yet).", "warning")
                return redirect(url_for('course_detail', id=module["course_id"]))

        cursor.execute("SELECT * FROM student_progress WHERE student_id = ? AND module_id = ?", (user_id, module_id))
        is_completed = cursor.fetchone() is not None

        cursor.execute("SELECT * FROM student_submissions WHERE student_id = ? AND module_id = ?", (user_id, module_id))
        submission = cursor.fetchone()
        student_answers = json.loads(submission["answers_json"]) if submission and submission["answers_json"] else {}

        seconds_left = None
        if current_user['role'] == 'STUDENT' and m_type in ('QUIZ', 'EXAM') and not is_completed and module['time_limit_minutes']:
            cursor.execute('SELECT started_at FROM assessment_attempts WHERE student_id = ? AND module_id = ?', (user_id, module_id))
            attempt = cursor.fetchone()
            if not attempt:
                cursor.execute('INSERT INTO assessment_attempts (student_id, module_id) VALUES (?, ?)', (user_id, module_id))
                conn.commit()
                cursor.execute('SELECT started_at FROM assessment_attempts WHERE student_id = ? AND module_id = ?', (user_id, module_id))
                attempt = cursor.fetchone()
            deadline = _parse_db_timestamp(attempt['started_at']) + timedelta(minutes=module['time_limit_minutes'])
            seconds_left = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
            if seconds_left == 0:
                _grade_assessment(cursor, user_id, module_id, json.loads(attempt['draft_answers_json'] or '{}'))
                conn.commit()
                return redirect(url_for('module_view', id=module_id))
        if current_user['role'] == 'STUDENT' and m_type in ('QUIZ', 'EXAM') and not is_completed:
            # Stable per-student question order; never changes when a page reloads.
            questions = list(questions)
            random.Random(f'{user_id}:{module_id}:question-order').shuffle(questions)
        teacher_submissions = []
        if current_user["role"] == "TEACHER":
            cursor.execute("""
                SELECT s.*, u.firstname, u.lastname, u.email FROM student_submissions s
                JOIN users u ON s.student_id = u.id
                WHERE s.module_id = ?
            """, (module_id,))
            teacher_submissions = cursor.fetchall()

        cursor.execute("SELECT id FROM modules WHERE course_id = ? AND id > ? ORDER BY id ASC LIMIT 1", (module["course_id"], module_id))
        next_mod = cursor.fetchone()
        next_module_id = next_mod["id"] if next_mod else None

    return render_template("module_view.html", user=current_user, module=module, course=course, questions=questions, 
                           content_blocks=content_blocks, next_module_id=next_module_id, is_completed=is_completed, 
                           student_answers=student_answers, submission=submission, teacher_submissions=teacher_submissions, seconds_left=seconds_left, grades_posted=bool(course["grades_posted"]))

@app.route("/student/complete_module", methods=["POST"])
def complete_module():
    if session.get("role") != "STUDENT": return "Unauthorized", 403
    module_id = request.form.get("module_id")
    user_id = session["user_id"]
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM modules WHERE id = ?', (module_id,))
        mod = cursor.fetchone()
        if not mod or (mod['type'] or '').upper() not in ('LESSON', 'LECTURE'):
            return 'Invalid lesson', 400
        cursor.execute('SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?', (user_id, mod['course_id']))
        if not cursor.fetchone(): return 'Not enrolled', 403
        allowed, reason = _module_access(cursor, mod, user_id)
        if not allowed: return reason, 403
        try:
            cursor.execute("INSERT INTO student_progress (student_id, module_id) VALUES (?, ?)", (user_id, module_id))
            conn.commit()
            flash("Module marked as completed!", "success")
        except Exception:
            pass
    return redirect(url_for("course_detail", id=request.form.get("course_id", 1)))

@app.route("/student/submit_assessment", methods=["POST"])
def submit_assessment():
    if session.get("role") != "STUDENT": return "Unauthorized", 403
    module_id = request.form.get("module_id")
    course_id = request.form.get("course_id")
    user_id = session["user_id"]
    
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM modules WHERE id = ?', (module_id,))
        mod = cursor.fetchone()
        if not mod or (mod['type'] or '').upper() not in ('QUIZ', 'EXAM'): return 'Invalid assessment', 400
        cursor.execute('SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?', (user_id, mod['course_id']))
        if not cursor.fetchone(): return 'Not enrolled', 403
        allowed, reason = _module_access(cursor, mod, user_id)
        if not allowed: return reason, 403
        cursor.execute('SELECT 1 FROM student_submissions WHERE student_id = ? AND module_id = ?', (user_id, module_id))
        if cursor.fetchone(): return 'Assessment already submitted', 409
        cursor.execute('SELECT records_locked FROM courses WHERE id = ?', (mod['course_id'],))
        if cursor.fetchone()['records_locked']: return 'Course records are locked', 403
        expired = False
        attempt = None
        if mod['time_limit_minutes']:
            cursor.execute('SELECT * FROM assessment_attempts WHERE student_id = ? AND module_id = ?', (user_id, module_id))
            attempt = cursor.fetchone()
            if not attempt: return 'Open the assessment before submitting', 403
            deadline = _parse_db_timestamp(attempt['started_at']) + timedelta(minutes=mod['time_limit_minutes'])
            expired = datetime.now(timezone.utc) >= deadline
        if expired:
            # A late request cannot replace the last draft saved before the deadline.
            saved_answers = json.loads(attempt['draft_answers_json'] or '{}')
            score, total = _grade_assessment(cursor, user_id, module_id, saved_answers)
            conn.commit()
            flash(f'Time expired. Saved answers submitted automatically: {score}/{total}. Unanswered questions count as wrong.', 'warning')
            return redirect(url_for('module_view', id=module_id))
        cursor.execute("SELECT * FROM quiz_questions WHERE module_id = ?", (module_id,))
        questions = cursor.fetchall()
        if not questions: return 'Assessment has no questions', 400
        score = 0
        answers = {}
        for q in questions:
            ans = request.form.get(f"q_{q['id']}")
            answers[str(q['id'])] = ans
            if ans and ' '.join(ans.split()).casefold() == ' '.join(q['correct_answer'].split()).casefold():
                score += 1
                
        total = len(questions)
        answers_json = json.dumps(answers)
        
        cursor.execute("""
            INSERT INTO student_submissions (student_id, module_id, score, total_questions, answers_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, module_id) DO UPDATE SET score=excluded.score, answers_json=excluded.answers_json, submitted_at=CURRENT_TIMESTAMP
        """, (user_id, module_id, score, total, answers_json))
        
        cursor.execute("INSERT INTO student_progress (student_id, module_id) VALUES (?, ?) ON CONFLICT(student_id, module_id) DO NOTHING", (user_id, module_id))
        conn.commit()

    flash(f"Assessment submitted successfully! Score: {score}/{total}", "success")
    return redirect(url_for("course_detail", id=course_id))

@app.route("/student/submit_assignment_file", methods=["POST"])
def submit_assignment_file():
    if session.get("role") != "STUDENT": return "Unauthorized", 403
    module_id = request.form.get("module_id")
    course_id = request.form.get("course_id")
    user_id = session["user_id"]
    
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM modules WHERE id = ?', (module_id,))
        mod = cur.fetchone()
        if not mod or (mod['type'] or '').upper() != 'ASSIGNMENT': return 'Invalid assignment', 400
        cur.execute('SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?', (user_id, mod['course_id']))
        if not cur.fetchone(): return 'Not enrolled', 403
        allowed, reason = _module_access(cur, mod, user_id)
        if not allowed: return reason, 403
        cur.execute('SELECT records_locked FROM courses WHERE id = ?', (mod['course_id'],))
        if cur.fetchone()['records_locked']: return 'Course records locked', 403
    submitted_file = request.files.get('assignment_file')
    if not submitted_file or not submitted_file.filename:
        flash('Please select your assignment file first.', 'danger')
        return redirect(url_for('module_view', id=module_id))
    try: file_filename = _save_upload(submitted_file)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('module_view', id=module_id))

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO student_submissions (student_id, module_id, file_filename)
            VALUES (?, ?, ?)
            ON CONFLICT(student_id, module_id) DO UPDATE SET file_filename=excluded.file_filename, submitted_at=CURRENT_TIMESTAMP
        """, (user_id, module_id, file_filename))
        
        cursor.execute("INSERT INTO student_progress (student_id, module_id) VALUES (?, ?) ON CONFLICT(student_id, module_id) DO NOTHING", (user_id, module_id))
        conn.commit()

    flash("Assignment submitted successfully!", "success")
    return redirect(url_for("course_detail", id=course_id))

@app.route("/teacher/grade_submission", methods=["POST"])
def grade_submission():
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    submission_id = request.form.get("submission_id")
    module_id = request.form.get("module_id")
    manual_grade = request.form.get("manual_grade")
    feedback = request.form.get("feedback")

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT c.* FROM courses c JOIN modules m ON m.course_id = c.id WHERE m.id = ?', (module_id,))
        course = cursor.fetchone()
        if not course or course['instructor_id'] != session.get('user_id'): return 'Unauthorized', 403
        if course['records_locked']: return 'Course records locked', 403
        cursor.execute("""
            UPDATE student_submissions SET manual_grade = ?, feedback = ? WHERE id = ?
        """, (manual_grade, feedback, submission_id))
        conn.commit()

    flash("Grade and feedback saved successfully!", "success")
    return redirect(url_for("module_view", id=module_id))

def _question_form_values(form):
    kind = (form.get('question_type') or 'multiple_choice').strip().lower()
    options = [(form.get('option_' + c.lower()) or '').strip() for c in 'ABCD']
    correct = (form.get('correct_answer') or '').strip()
    if kind != 'identification':
        correct = correct.upper()
    if kind == 'identification':
        options = [None, None, None, None]
    if kind == 'true_false':
        options = ['True', 'False', None, None]
        if correct == 'TRUE': correct = 'A'
        if correct == 'FALSE': correct = 'B'
    return kind, options, correct


def _question_valid(kind, options, correct):
    return ((kind == 'multiple_choice' and all(options) and correct in ('A','B','C','D'))
            or (kind == 'true_false' and correct in ('A','B'))
            or (kind == 'identification' and bool(correct) and len(correct) <= 500))


@app.route("/teacher/add_question", methods=["POST"])
def add_question():
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    module_id = request.form.get('module_id', type=int)
    question = (request.form.get('question_text') or '').strip()
    kind, options, correct = _question_form_values(request.form)
    if not question or not _question_valid(kind, options, correct): return 'Invalid question', 400
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT course_id, type FROM modules WHERE id = ?', (module_id,))
        mod = cur.fetchone()
        if not mod or (mod['type'] or '').upper() not in ('QUIZ', 'EXAM') or not _course_teacher(cur, mod['course_id']): return 'Unauthorized', 403
        cur.execute('INSERT INTO quiz_questions (module_id, question_text, question_type, option_a, option_b, option_c, option_d, correct_answer) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (module_id, question, kind, *options, correct))
        conn.commit()
    flash('Question added successfully!', 'success')
    return redirect(url_for('module_view', id=module_id))


@app.route("/teacher/edit_question", methods=["POST"])
def edit_question():
    if session.get('role') != 'TEACHER': return 'Unauthorized', 403
    q_id = request.form.get('q_id', type=int)
    question = (request.form.get('question_text') or '').strip()
    kind, options, correct = _question_form_values(request.form)
    if not question or not _question_valid(kind, options, correct): return 'Invalid question', 400
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT m.course_id, m.id AS module_id FROM quiz_questions q JOIN modules m ON q.module_id = m.id WHERE q.id = ?', (q_id,))
        item = cur.fetchone()
        if not item or not _course_teacher(cur, item['course_id']): return 'Unauthorized', 403
        cur.execute('UPDATE quiz_questions SET question_text = ?, question_type = ?, option_a = ?, option_b = ?, option_c = ?, option_d = ?, correct_answer = ? WHERE id = ?', (question, kind, *options, correct, q_id))
        conn.commit()
    flash('Question updated successfully!', 'success')
    return redirect(url_for('module_view', id=item['module_id']))

@app.route("/teacher/delete_question/<int:q_id>", methods=["POST"])
def delete_question(q_id):
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    module_id = request.form.get("module_id")
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM quiz_questions WHERE id = ?", (q_id,))
        conn.commit()
    flash("Question deleted successfully.", "success")
    return redirect(url_for("module_view", id=module_id))

@app.route("/teacher/add_content_block", methods=["POST"])
def add_content_block():
    if session.get('role') != 'TEACHER': return 'Unauthorized', 403
    try: module_id = int(request.form.get('module_id', ''))
    except ValueError: return 'Invalid module', 400
    block_type = (request.form.get('block_type') or '').upper()
    content = (request.form.get('block_content') or '').strip()
    uploaded = request.files.get('block_file')
    if block_type not in ('TEXT', 'FILE', 'LINK'):
        flash('Choose a supported block type.', 'danger')
        return redirect(url_for('module_view', id=module_id))
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT course_id FROM modules WHERE id=?', (module_id,))
        module = cur.fetchone()
        if not module or not _course_teacher(cur, module['course_id']): return 'Unauthorized', 403
        file_name = None
        if block_type == 'FILE':
            if not uploaded or not uploaded.filename:
                flash('Select a file to upload.', 'danger')
                return redirect(url_for('module_view', id=module_id))
            try: file_name = _save_upload(uploaded)
            except ValueError as exc:
                flash(str(exc), 'danger')
                return redirect(url_for('module_view', id=module_id))
            content = content or secure_filename(uploaded.filename)
        elif block_type == 'LINK' and not _valid_external_url(content):
            flash('Enter a valid http:// or https:// URL.', 'danger')
            return redirect(url_for('module_view', id=module_id))
        elif block_type == 'TEXT' and not content:
            flash('Enter text for this content block.', 'danger')
            return redirect(url_for('module_view', id=module_id))
        cur.execute('INSERT INTO content_blocks (module_id, block_type, block_content, file_name) VALUES (?, ?, ?, ?)',
                    (module_id, block_type, content, file_name))
        conn.commit()
    flash('Content block added successfully!', 'success')
    return redirect(url_for('module_view', id=module_id))

@app.route("/teacher/edit_content_block", methods=["POST"])
def edit_content_block():
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    block_id = request.form.get("block_id")
    module_id = request.form.get("module_id")
    block_content = request.form.get("block_content", "")

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE content_blocks SET block_content = ? WHERE id = ?
        """, (block_content, block_id))
        conn.commit()

    flash("Content block updated successfully!", "success")
    return redirect(url_for("module_view", id=module_id))

@app.route("/teacher/delete_content_block/<int:block_id>", methods=["POST"])
def delete_content_block(block_id):
    if session.get("role") != "TEACHER": return "Unauthorized", 403
    module_id = request.form.get("module_id")
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT m.course_id FROM content_blocks b JOIN modules m ON b.module_id=m.id WHERE b.id=? AND m.id=?', (block_id, module_id))
        target=cursor.fetchone()
        if not target or not _course_teacher(cursor, target['course_id']): return 'Unauthorized', 403
        cursor.execute("DELETE FROM content_blocks WHERE id = ?", (block_id,))
        conn.commit()
    flash("Content block removed.", "success")
    return redirect(url_for("module_view", id=module_id))

@app.route('/course/<int:course_id>/gradebook')
def gradebook(course_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    with closing(get_connection()) as conn:
        cur=conn.cursor()
        course=_grade_access(cur,course_id)
        if not course: return 'Not authorized to view this gradebook',403
        is_teacher=session.get('role')=='TEACHER'
        cur.execute('SELECT id, firstname, lastname, email FROM users WHERE id=?',(session['user_id'],))
        user=cur.fetchone()
        if is_teacher:
            cur.execute('''SELECT u.id,u.firstname,u.lastname,u.email FROM users u
                JOIN enrollments e ON e.student_id=u.id WHERE e.course_id=?
                ORDER BY u.lastname,u.firstname''',(course_id,))
            students=[dict(x) for x in cur.fetchall()]
        else: students=[dict(user)]
        view_id=request.args.get('student_id',type=int) if is_teacher else session['user_id']
        if not view_id and students:view_id=students[0]['id']
        selected=next((x for x in students if x['id']==view_id),None)
        if view_id and not selected: return 'Student not enrolled',403
        rows=_grade_rows(cur,course_id,view_id) if view_id else []
        summary=_grade_summary(rows)
        roster=[]
        if is_teacher:
            for student in students:
                ss=_grade_summary(_grade_rows(cur,course_id,student['id']))
                roster.append({**student,'overall':ss['overall'],'graded':ss['graded']})
    return render_template('gradebook.html',course=course,user=user,students=students,selected=selected,
        rows=rows,summary=summary,roster=roster,is_teacher=is_teacher)


@app.route('/teacher/update_grade',methods=['POST'])
def update_grade():
    if session.get('role')!='TEACHER': return 'Unauthorized',403
    course_id=request.form.get('course_id',type=int)
    student_id=request.form.get('student_id',type=int)
    module_id=request.form.get('module_id',type=int)
    raw=request.form.get('percentage','').strip()
    feedback=request.form.get('feedback','').strip()
    if not all([course_id,student_id,module_id]):return 'Missing fields',400
    try:
        grade=float(raw) if raw else None
        if grade is not None and (not 0<=grade<=100 or not __import__('math').isfinite(grade)):raise ValueError()
    except ValueError:
        flash('Enter a percentage from 0 to 100.', 'danger')
        return redirect(url_for('gradebook',course_id=course_id,student_id=student_id))
    with closing(get_connection()) as conn:
        cur=conn.cursor()
        course=_course_teacher(cur,course_id)
        if not course:return 'Unauthorized',403
        if course['records_locked']:return 'Course records are locked',403
        cur.execute("SELECT id FROM modules WHERE id=? AND course_id=? AND UPPER(type) IN ('QUIZ','EXAM','ASSIGNMENT')",(module_id,course_id))
        if not cur.fetchone():return 'Invalid module',400
        cur.execute('SELECT 1 FROM enrollments WHERE student_id=? AND course_id=?',(student_id,course_id))
        if not cur.fetchone():return 'Student not enrolled',403
        cur.execute('''INSERT INTO student_submissions (student_id,module_id,manual_grade,feedback)
            VALUES (?,?,?,?) ON CONFLICT(student_id,module_id)
            DO UPDATE SET manual_grade=excluded.manual_grade, feedback=excluded.feedback''',
            (student_id,module_id,str(grade) if grade is not None else None,feedback))
        conn.commit()
    flash('Grade saved.','success')
    return redirect(url_for('gradebook',course_id=course_id,student_id=student_id))

@app.route('/teacher/course_records', methods=['POST'])
def course_records():
    if session.get('role') != 'TEACHER': return 'Unauthorized', 403
    course_id = request.form.get('course_id', type=int)
    action = request.form.get('action')
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        course = _course_teacher(cursor, course_id)
        if not course: return 'Unauthorized', 403
        if action == 'post' and not course['records_locked']:
            cursor.execute('UPDATE courses SET grades_posted = TRUE WHERE id = ?', (course_id,))
        elif action == 'lock' and course['grades_posted']:
            cursor.execute('UPDATE courses SET records_locked = TRUE WHERE id = ?', (course_id,))
        else: return 'Invalid action or record already locked', 400
        conn.commit()
    flash('Course records updated.', 'success')
    return redirect(url_for('course_detail', id=course_id))


@app.route('/teacher/course_export/<int:course_id>/<kind>')
def course_export(course_id, kind):
    if session.get('role') != 'TEACHER': return 'Unauthorized', 403
    if kind not in ('roster', 'grades', 'syllabus'): return 'Invalid export', 404
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        course = _course_teacher(cur, course_id)
        if not course: return 'Unauthorized', 403
        output = io.StringIO()
        writer = csv.writer(output)
        if kind == 'syllabus':
            writer.writerow(['Course', 'Code', 'Description'])
            writer.writerow([course['title'], course['code'], course['description']])
            writer.writerow(['Module', 'Type', 'Section', 'Due Date', 'Pass Percentage', 'Time Limit (minutes)'])
            cur.execute('SELECT * FROM modules WHERE course_id = ? ORDER BY id', (course_id,))
            for row in cur.fetchall(): writer.writerow([row['title'], row['type'], row['section_name'], row['due_date'], row['pass_percentage'], row['time_limit_minutes']])
        elif kind == 'roster':
            writer.writerow(['First Name', 'Last Name', 'Email', 'Date Enrolled'])
            cur.execute('SELECT u.firstname, u.lastname, u.email, e.enrolled_at FROM enrollments e JOIN users u ON u.id = e.student_id WHERE e.course_id = ? ORDER BY u.lastname, u.firstname', (course_id,))
            for r in cur.fetchall(): writer.writerow(list(r))
        else:
            writer.writerow(['Last Name', 'First Name', 'Email', 'Module', 'Type', 'Score', 'Total', 'Manual Grade', 'Feedback', 'Submitted At'])
            cur.execute("""SELECT u.lastname, u.firstname, u.email, m.title, m.type, s.score, s.total_questions, s.manual_grade, s.feedback, s.submitted_at
                FROM student_submissions s JOIN users u ON u.id = s.student_id JOIN modules m ON m.id = s.module_id
                WHERE m.course_id = ? ORDER BY u.lastname, u.firstname, m.id""", (course_id,))
            for r in cur.fetchall(): writer.writerow(list(r))
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="course_{course_id}_{kind}.csv"'})


@app.route("/calendar.html")
@app.route("/calendar")
def calendar():
    if "user_id" not in session: return redirect(url_for("login"))
    user_id = session["user_id"]
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        
        # User-created events remain editable. Course deadlines are generated live
        # from modules, so new/edited/deleted modules stay in sync automatically.
        cursor.execute("SELECT * FROM calendar_events WHERE user_id = ?", (user_id,))
        personal_events = [dict(row) for row in cursor.fetchall()]
        if current_user['role'] == 'STUDENT':
            cursor.execute("""SELECT m.id, m.title, m.type, m.due_date, c.code AS course_code
                FROM modules m JOIN courses c ON c.id=m.course_id
                JOIN enrollments e ON e.course_id=c.id
                WHERE e.student_id=? AND m.due_date IS NOT NULL AND m.due_date != ''
                ORDER BY m.due_date""", (user_id,))
        else:
            cursor.execute("""SELECT m.id, m.title, m.type, m.due_date, c.code AS course_code
                FROM modules m JOIN courses c ON c.id=m.course_id
                WHERE c.instructor_id=? AND m.due_date IS NOT NULL AND m.due_date != ''
                ORDER BY m.due_date""", (user_id,))
        type_colors = {'QUIZ':'bg-warning', 'EXAM':'bg-danger', 'ASSIGNMENT':'bg-primary', 'LECTURE':'bg-success'}
        for mod in cursor.fetchall():
            kind = (mod['type'] or 'MODULE').upper()
            personal_events.append({
                'id': f"mod_{mod['id']}",
                'title': f"[{mod['course_code']}] {kind.title()}: {mod['title']}",
                'event_date': str(mod['due_date'])[:10],
                'category_color': type_colors.get(kind, 'bg-secondary'),
                'description': f"{kind.title()} due {mod['due_date']}",
                'is_course_deadline': True
            })

    return render_template("calendar.html", user=current_user, events=personal_events)

@app.route("/calendar/add_event", methods=["POST"])
def add_event():
    if "user_id" not in session: return redirect(url_for("login"))
    title = request.form.get("title")
    date = request.form.get("event_date")
    color = request.form.get("category_color")
    description = request.form.get("description")
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO calendar_events (user_id, title, event_date, category_color, description) VALUES (?, ?, ?, ?, ?)", 
                       (session["user_id"], title, date, color, description))
        conn.commit()
    flash("Event added to calendar!", "success")
    return redirect(url_for("calendar"))

@app.route("/calendar/delete_event/<int:event_id>", methods=["POST"])
def delete_event(event_id):
    if "user_id" not in session: return redirect(url_for("login"))
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, session["user_id"]))
        conn.commit()
    flash("Event removed successfully.", "success")
    return redirect(url_for("calendar"))

@app.route("/mailbox.html")
@app.route("/mailbox")
def mailbox():
    if "user_id" not in session: return redirect(url_for("login"))
    user_id = session["user_id"]
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        cursor.execute("""
            SELECT m.*, u.email as sender_email FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.recipient_id = ? OR m.sender_id = ? ORDER BY m.sent_at DESC
        """, (user_id, user_id))
        messages = cursor.fetchall()
    return render_template("mailbox.html", user=current_user, messages=messages)

@app.route("/send_message", methods=["POST"])
def send_message():
    if "user_id" not in session: return redirect(url_for("login"))
    recipient_email = request.form.get("recipient_email")
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (recipient_email,))
        recipient = cursor.fetchone()
        if not recipient:
            flash("Recipient email not found.", "danger")
        else:
            cursor.execute("INSERT INTO messages (sender_id, recipient_id, subject, body) VALUES (?, ?, ?, ?)",
                           (session["user_id"], recipient["id"], request.form.get("subject"), request.form.get("body")))
            conn.commit()
            flash("Message sent successfully!", "success")
    return redirect(url_for("mailbox"))

@app.route("/notifications.html")
@app.route("/notifications")
def notifications():
    if "user_id" not in session: return redirect(url_for("login"))
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],))
        current_user = cursor.fetchone()
        cursor.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (session["user_id"],))
        notifications_list = cursor.fetchall()
    return render_template("notifications.html", user=current_user, notifications=notifications_list)

@app.route("/settings.html", methods=["GET", "POST"])
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "user_id" not in session: return redirect(url_for("login"))
    user_id = session["user_id"]
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        if request.method == "POST":
            cursor.execute("UPDATE users SET email = ? WHERE id = ?", (request.form.get("email"), user_id))
            conn.commit()
            flash("Profile updated successfully!", "success")
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
    return render_template("settings.html", user=current_user)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True, port=5000)
