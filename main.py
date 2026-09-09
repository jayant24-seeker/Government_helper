import os
import json
import uuid
import enum
import re
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from urllib.request import urlopen
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
secret = os.environ.get("SECRET_KEY")
if not secret:
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY must be set in production.")
    secret = "dev-only-change-me"
app.secret_key = secret
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR/'citizenconnect.db'}"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    UPLOAD_FOLDER=str(UPLOAD_FOLDER),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
ALLOWED_PROOF_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
ISSUE_TYPES = {"Broken Pothole", "Garbage Overflow", "Streetlight Not Working"}
STATUS_VALUES = {"Pending", "In Progress", "Resolved", "Rejected"}

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
db.init_app(app)

class ComplaintStatus(enum.Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    REJECTED = "Rejected"

class Citizen(db.Model):
    __tablename__ = "citizens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(250), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    alt_phone_number: Mapped[str | None] = mapped_column(String(15), nullable=True)
    permanent_address: Mapped[str] = mapped_column(Text, nullable=False)
    current_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_proof_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    password: Mapped[str] = mapped_column(String(250), nullable=False)
    complaints = relationship("Complaint", back_populates="citizen", cascade="all, delete-orphan")

class Staff(db.Model):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(250), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    sector: Mapped[str] = mapped_column(String(250), nullable=False)
    proof_path: Mapped[str] = mapped_column(String(500), nullable=True)
    password: Mapped[str] = mapped_column(String(250), nullable=False)
    complaints = relationship("Complaint", back_populates="staff")

class Complaint(db.Model):
    __tablename__ = "complaints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    citizen_id: Mapped[int] = mapped_column(Integer, ForeignKey("citizens.id"), nullable=False)
    staff_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("staff.id"), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolution_proof_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolution_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    gps_location: Mapped[str] = mapped_column(String(250), nullable=False)
    pincode: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[ComplaintStatus] = mapped_column(db.Enum(ComplaintStatus), default=ComplaintStatus.PENDING, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    citizen = relationship("Citizen", back_populates="complaints")
    staff = relationship("Staff", back_populates="complaints")

def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits

def valid_phone(value):
    return bool(re.fullmatch(r"[6-9]\d{9}", normalize_phone(value)))

def valid_pincode(value):
    return bool(re.fullmatch(r"\d{6}", (value or "").strip()))

def allowed_file(filename, allowed):
    return bool(filename and "." in filename and filename.rsplit(".", 1)[1].lower() in allowed)

def save_upload(file, allowed):
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename, allowed):
        raise ValueError("Unsupported file type.")
    ext = secure_filename(file.filename).rsplit(".", 1)[-1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    file.save(UPLOAD_FOLDER / filename)
    return filename

def track_id(complaint_id):
    return f"CZN-{complaint_id:06d}"

def parse_track_id(value):
    m = re.fullmatch(r"CZN-(\d{1,12})", (value or "").strip().upper())
    return int(m.group(1)) if m else None

def complaint_dict(c, include_citizen=False):
    data = {
        "id": c.id,
        "track_id": track_id(c.id),
        "issue_type": c.issue_type,
        "description": c.description,
        "gps_location": c.gps_location,
        "pincode": c.pincode,
        "status": c.status.value if isinstance(c.status, ComplaintStatus) else str(c.status),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "last_updated": c.last_updated.isoformat() if c.last_updated else None,
        "image_url": url_for("uploaded_file", filename=c.image_path) if c.image_path else None,
        "resolution_proof_url": url_for("uploaded_file", filename=c.resolution_proof_path) if c.resolution_proof_path else None,
        "resolution_comments": c.resolution_comments,
        "staff_id": c.staff_id,
        "staff_name": c.staff.full_name if c.staff else None,
    }
    if include_citizen:
        data["citizen"] = {
            "id": c.citizen.id,
            "name": c.citizen.full_name,
            "phone": c.citizen.phone_number,
        }
    return data

def login_required(role):
    key = f"{role}_id"
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get(key):
                if request.path.startswith("/api/"):
                    return jsonify({"success": False, "error": "Authentication required."}), 401
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login_page"))
            return fn(*args, **kwargs)
        return wrapped
    return deco

def get_current(role):
    model = Citizen if role == "citizen" else Staff
    return db.session.get(model, session.get(f"{role}_id"))

def generate_ai_description(issue_type, location, pincode, user_description=""):
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not GENAI_AVAILABLE or not api_key:
        base = f"Issue reported as {issue_type} at {location}, Pincode {pincode}."
        return f"{base} {user_description}".strip()
    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            "Write a concise formal civic complaint description in 2-3 sentences. "
            f"Issue: {issue_type}. Location: {location}. Pincode: {pincode}. "
            f"Citizen notes: {user_description or 'None'}. "
            "Do not invent facts."
        )
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return (response.text or "").strip() or f"Issue reported as {issue_type} at {location}, Pincode {pincode}."
    except Exception as exc:
        app.logger.warning("Gemini description failed: %s", exc)
        return f"Issue reported as {issue_type} at {location}, Pincode {pincode}. {user_description}".strip()

def analyze_with_ai(file):
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not GENAI_AVAILABLE or not api_key:
        return None
    try:
        data = file.read()
        client = genai.Client(api_key=api_key)
        prompt = (
            "Analyze this civic complaint image. Choose exactly one issue_type from "
            "Broken Pothole, Garbage Overflow, Streetlight Not Working. "
            "Return ONLY JSON with issue_type and description. Do not invent details."
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[types.Part.from_bytes(data=data, mime_type=file.content_type or "image/jpeg"), prompt],
        )
        text = (response.text or "").replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        if result.get("issue_type") not in ISSUE_TYPES:
            result["issue_type"] = "Broken Pothole"
        return result
    except Exception as exc:
        app.logger.warning("Gemini image analysis failed: %s", exc)
        return None

with app.app_context():
    db.create_all()
    # Lightweight migration for the supplied SQLite database. Fresh databases need nothing.
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        columns = {c["name"] for c in inspector.get_columns("citizens")}
        if "address_proof_path" not in columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE citizens ADD COLUMN address_proof_path VARCHAR(500)"))
        complaint_columns = {c["name"] for c in inspector.get_columns("complaints")}
        with db.engine.begin() as conn:
            if "resolution_proof_path" not in complaint_columns:
                conn.execute(text("ALTER TABLE complaints ADD COLUMN resolution_proof_path VARCHAR(500)"))
            if "resolution_comments" not in complaint_columns:
                conn.execute(text("ALTER TABLE complaints ADD COLUMN resolution_comments TEXT"))

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/ask")
def sign_ask():
    return render_template("sign_asker.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/signup-citizen")
def signup_citizen_page():
    return render_template("sign_in_citizen.html")

@app.route("/signup-staff")
def signup_staff_page():
    return render_template("sign_asker_govt.html")

@app.route("/citizen-dashboard")
@login_required("citizen")
def citizen_dashboard():
    return render_template("citizen_dashboard.html")

@app.route("/staff-dashboard")
@login_required("staff")
def staff_dashboard():
    return render_template("staff.html")

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # Citizens can only see files attached to their own complaints; staff can see all.
    if not session.get("citizen_id") and not session.get("staff_id"):
        return jsonify({"success": False, "error": "Authentication required."}), 401
    safe = os.path.basename(filename)
    if session.get("staff_id"):
        return send_from_directory(app.config["UPLOAD_FOLDER"], safe)
    citizen = get_current("citizen")
    owns = db.session.scalar(db.select(Complaint.id).where(
        Complaint.citizen_id == citizen.id,
        (Complaint.image_path == safe) | (Complaint.resolution_proof_path == safe)
    ))
    if not owns:
        return jsonify({"success": False, "error": "Forbidden."}), 403
    return send_from_directory(app.config["UPLOAD_FOLDER"], safe)

@app.route("/register-citizen", methods=["POST"])
def register_citizen():
    phone = normalize_phone(request.form.get("phone", ""))
    password = request.form.get("password", "")
    confirm = request.form.get("confirmPassword", "")
    name = request.form.get("fullName", "").strip()
    permanent = request.form.get("permanentAddress", "").strip()
    current = request.form.get("currentAddress", "").strip() or None
    if not valid_phone(phone):
        flash("Enter a valid 10-digit Indian mobile number.", "error")
        return redirect(url_for("signup_citizen_page"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("signup_citizen_page"))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("signup_citizen_page"))
    if not name or not permanent:
        flash("Name and permanent address are required.", "error")
        return redirect(url_for("signup_citizen_page"))
    try:
        proof = save_upload(request.files.get("addressProof"), ALLOWED_PROOF_EXTENSIONS)
        user = Citizen(full_name=name, phone_number=phone,
                       alt_phone_number=normalize_phone(request.form.get("altPhone")) or None,
                       permanent_address=permanent, current_address=current,
                       address_proof_path=proof, password=generate_password_hash(password))
        db.session.add(user); db.session.commit()
        flash("Registration successful. You can now log in.", "success")
        return redirect(url_for("login_page"))
    except IntegrityError:
        db.session.rollback()
        flash("This phone number is already registered.", "error")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    except Exception:
        db.session.rollback(); app.logger.exception("Citizen registration failed")
        flash("Registration failed. Please try again.", "error")
    return redirect(url_for("signup_citizen_page"))

@app.route("/register-staff", methods=["POST"])
def register_staff():
    phone = normalize_phone(request.form.get("phone", ""))
    password = request.form.get("password", "")
    confirm = request.form.get("confirmPassword", "")
    name = request.form.get("name", "").strip()
    sector = request.form.get("sector", "").strip()
    if not valid_phone(phone):
        flash("Enter a valid 10-digit Indian mobile number.", "error")
        return redirect(url_for("signup_staff_page"))
    if len(password) < 8 or password != confirm:
        flash("Use an 8+ character password and make sure both passwords match.", "error")
        return redirect(url_for("signup_staff_page"))
    if not name or not sector:
        flash("Name and sector are required.", "error")
        return redirect(url_for("signup_staff_page"))
    try:
        proof = save_upload(request.files.get("proof"), ALLOWED_PROOF_EXTENSIONS)
        user = Staff(full_name=name, phone_number=phone, sector=sector, proof_path=proof,
                     password=generate_password_hash(password))
        db.session.add(user); db.session.commit()
        flash("Staff registration successful. You can now log in.", "success")
        return redirect(url_for("login_page"))
    except IntegrityError:
        db.session.rollback(); flash("This phone number is already registered.", "error")
    except ValueError as exc:
        db.session.rollback(); flash(str(exc), "error")
    except Exception:
        db.session.rollback(); app.logger.exception("Staff registration failed")
        flash("Registration failed. Please try again.", "error")
    return redirect(url_for("signup_staff_page"))

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or request.form
    phone = normalize_phone(data.get("phone", ""))
    password = data.get("password", "")
    role = data.get("user_type", "")
    if not valid_phone(phone) or not password or role not in {"citizen", "staff"}:
        return jsonify({"success": False, "error": "Enter a valid phone number, password and role."}), 400
    model = Citizen if role == "citizen" else Staff
    user = db.session.scalar(db.select(model).where(model.phone_number == phone))
    if not user or not check_password_hash(user.password, password):
        return jsonify({"success": False, "error": "Invalid phone number or password."}), 401
    session.clear()
    session[f"{role}_id"] = user.id
    session["role"] = role
    return jsonify({"success": True, "redirect_url": url_for("citizen_dashboard" if role == "citizen" else "staff_dashboard")})

# OTP has intentionally been removed. Password reset is a direct authenticated-by-phone
# convenience flow as requested; production deployments should add email/admin recovery
# if stronger account recovery is required.
@app.route("/api/password-reset", methods=["POST"])
def password_reset():
    data = request.get_json(silent=True) or {}
    phone = normalize_phone(data.get("phone", ""))
    password = data.get("password", "")
    confirm = data.get("confirm_password", "")
    if not valid_phone(phone) or len(password) < 8 or password != confirm:
        return jsonify({"success": False, "error": "Enter a valid phone number and matching 8+ character password."}), 400
    user = db.session.scalar(db.select(Citizen).where(Citizen.phone_number == phone))
    if not user:
        return jsonify({"success": False, "error": "No citizen account found for that phone number."}), 404
    user.password = generate_password_hash(password)
    db.session.commit()
    return jsonify({"success": True, "message": "Password updated successfully."})

@app.route("/api/analyze-image", methods=["POST"])
@login_required("citizen")
def analyze_image():
    file = request.files.get("photo_proof")
    if not file or not allowed_file(file.filename, ALLOWED_IMAGE_EXTENSIONS):
        return jsonify({"success": False, "error": "Please upload a JPG, JPEG or PNG image."}), 400
    result = analyze_with_ai(file)
    if not result:
        return jsonify({"success": False, "error": "AI analysis is unavailable. You can select the issue manually."}), 503
    return jsonify({"success": True, **result})

@app.route("/lodge-complaint", methods=["POST"])
@login_required("citizen")
def lodge_complaint():
    issue = request.form.get("issue_type", "").strip()
    description = request.form.get("description", "").strip()
    pincode = request.form.get("pincode", "").strip()
    gps = request.form.get("gps_location", "").strip()
    if issue not in ISSUE_TYPES:
        return jsonify({"success": False, "error": "Select a valid issue type."}), 400
    if not description:
        return jsonify({"success": False, "error": "Description is required."}), 400
    if not valid_pincode(pincode):
        return jsonify({"success": False, "error": "Enter a valid 6-digit pincode."}), 400
    if not gps or gps.lower().startswith("could not"):
        return jsonify({"success": False, "error": "A valid GPS location is required."}), 400
    try:
        image = save_upload(request.files.get("photo_proof"), ALLOWED_IMAGE_EXTENSIONS)
        complaint = Complaint(citizen_id=session["citizen_id"], issue_type=issue,
                              description=generate_ai_description(issue, gps, pincode, description),
                              image_path=image, gps_location=gps, pincode=pincode,
                              status=ComplaintStatus.PENDING)
        db.session.add(complaint); db.session.commit()
        return jsonify({"success": True, "track_id": track_id(complaint.id), "complaint": complaint_dict(complaint)})
    except ValueError as exc:
        db.session.rollback(); return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        db.session.rollback(); app.logger.exception("Complaint creation failed")
        return jsonify({"success": False, "error": "Unable to submit complaint."}), 500

@app.route("/api/citizen/complaints")
@login_required("citizen")
def citizen_complaints():
    complaints = db.session.scalars(db.select(Complaint).where(
        Complaint.citizen_id == session["citizen_id"]).order_by(Complaint.created_at.desc())).all()
    return jsonify({"success": True, "complaints": [complaint_dict(c) for c in complaints]})

@app.route("/api/complaints/<track>")
@login_required("citizen")
def get_citizen_complaint(track):
    cid = parse_track_id(track)
    if cid is None:
        return jsonify({"success": False, "error": "Invalid Track ID. Use CZN-000001 format."}), 400
    c = db.session.scalar(db.select(Complaint).where(
        Complaint.id == cid, Complaint.citizen_id == session["citizen_id"]))
    if not c:
        return jsonify({"success": False, "error": "Complaint not found."}), 404
    return jsonify({"success": True, "complaint": complaint_dict(c)})

@app.route("/api/staff/complaints")
@login_required("staff")
def staff_complaints():
    status = request.args.get("status")
    query = db.select(Complaint).order_by(Complaint.created_at.desc())
    if status in STATUS_VALUES:
        query = query.where(Complaint.status == ComplaintStatus(status))
    complaints = db.session.scalars(query).all()
    return jsonify({"success": True, "complaints": [complaint_dict(c, True) for c in complaints]})

@app.route("/api/staff/stats")
@login_required("staff")
def staff_stats():
    total = db.session.scalar(db.select(func.count(Complaint.id))) or 0
    pending = db.session.scalar(db.select(func.count(Complaint.id)).where(Complaint.status == ComplaintStatus.PENDING)) or 0
    in_progress = db.session.scalar(db.select(func.count(Complaint.id)).where(Complaint.status == ComplaintStatus.IN_PROGRESS)) or 0
    resolved = db.session.scalar(db.select(func.count(Complaint.id)).where(Complaint.status == ComplaintStatus.RESOLVED)) or 0
    return jsonify({"success": True, "total": total, "pending": pending, "in_progress": in_progress, "resolved": resolved})

@app.route("/api/staff/complaints/<int:complaint_id>", methods=["GET"])
@login_required("staff")
def staff_complaint_detail(complaint_id):
    c = db.session.get(Complaint, complaint_id)
    if not c:
        return jsonify({"success": False, "error": "Complaint not found."}), 404
    return jsonify({"success": True, "complaint": complaint_dict(c, True)})

@app.route("/api/staff/complaints/<int:complaint_id>/claim", methods=["POST"])
@login_required("staff")
def claim_complaint(complaint_id):
    c = db.session.get(Complaint, complaint_id)
    if not c:
        return jsonify({"success": False, "error": "Complaint not found."}), 404
    if c.staff_id and c.staff_id != session["staff_id"]:
        return jsonify({"success": False, "error": "This complaint is already assigned to another staff member."}), 409
    c.staff_id = session["staff_id"]
    if c.status == ComplaintStatus.PENDING:
        c.status = ComplaintStatus.IN_PROGRESS
    db.session.commit()
    return jsonify({"success": True, "complaint": complaint_dict(c, True)})

@app.route("/api/staff/complaints/<int:complaint_id>/status", methods=["POST"])
@login_required("staff")
def update_complaint_status(complaint_id):
    c = db.session.get(Complaint, complaint_id)
    if not c:
        return jsonify({"success": False, "error": "Complaint not found."}), 404
    if c.staff_id not in {None, session["staff_id"]}:
        return jsonify({"success": False, "error": "Complaint is assigned to another staff member."}), 403
    data = request.form if request.form else (request.get_json(silent=True) or {})
    status = data.get("status", "")
    if status not in STATUS_VALUES:
        return jsonify({"success": False, "error": "Invalid status."}), 400
    c.staff_id = session["staff_id"]
    c.status = ComplaintStatus(status)
    c.resolution_comments = (data.get("comments") or "").strip() or c.resolution_comments
    try:
        proof = save_upload(request.files.get("resolution_proof"), ALLOWED_PROOF_EXTENSIONS)
        if proof:
            c.resolution_proof_path = proof
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    db.session.commit()
    return jsonify({"success": True, "complaint": complaint_dict(c, True)})

@app.route("/api/profile", methods=["GET", "POST"])
@login_required("citizen")
def profile():
    user = get_current("citizen")
    if request.method == "GET":
        return jsonify({"success": True, "profile": {
            "full_name": user.full_name, "phone_number": user.phone_number,
            "alt_phone_number": user.alt_phone_number,
            "permanent_address": user.permanent_address,
            "current_address": user.current_address,
        }})
    data = request.get_json(silent=True) or {}
    user.full_name = (data.get("full_name") or user.full_name).strip()
    user.alt_phone_number = normalize_phone(data.get("alt_phone_number", "")) or None
    user.permanent_address = (data.get("permanent_address") or user.permanent_address).strip()
    user.current_address = (data.get("current_address") or "").strip() or None
    db.session.commit()
    return jsonify({"success": True, "message": "Profile updated."})

@app.route("/get-location-by-pincode", methods=["POST"])
def get_location_by_pincode():
    pincode = ((request.get_json(silent=True) or {}).get("pincode") or "").strip()
    if not valid_pincode(pincode):
        return jsonify({"success": False, "error": "Enter a valid 6-digit pincode."}), 400
    try:
        with urlopen(f"https://api.postalpincode.in/pincode/{quote(pincode)}", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload and payload[0].get("Status") == "Success" and payload[0].get("PostOffice"):
            office = payload[0]["PostOffice"][0]
            return jsonify({"success": True, "location": {"city": office.get("District"), "state": office.get("State"), "office": office.get("Name")}})
        return jsonify({"success": False, "error": "Pincode not found."}), 404
    except Exception as exc:
        app.logger.warning("Pincode lookup failed: %s", exc)
        return jsonify({"success": False, "error": "Location service is temporarily unavailable."}), 503

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))

@app.errorhandler(413)
def too_large(_):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "File too large. Maximum upload size is 8 MB."}), 413
    flash("File too large. Maximum upload size is 8 MB.", "error")
    return redirect(request.referrer or url_for("home"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5003)), debug=os.environ.get("FLASK_DEBUG") == "1")
