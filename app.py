from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
import config
import mysql.connector as connector
from werkzeug.utils import secure_filename
import os
from ultralytics import YOLO
import bcrypt
from collections import Counter
from dotenv import load_dotenv
from functools import wraps
from datetime import datetime, timedelta
import csv
import io

from utils.severity import (
    extract_and_classify_detections,
    calculate_pricing_with_severity,
    SEVERITY_MULTIPLIERS,
    SEVERITY_COLORS,
    SEVERITY_BADGE_CLASSES
)

from utils.insurance import (
    calculate_claim_coverage,
    check_auto_approval,
    create_claim_record,
    generate_claim_pdf,
    POLICY_COVERAGE
)

from utils.triage import (
    calculate_triage_score,
    evaluate_and_update_report_triage,
    refresh_sla_status_all,
    PRIORITY_CONFIG
)


load_dotenv()


app = Flask(__name__)
app.secret_key = config.SECRET_KEY if hasattr(config, 'SECRET_KEY') else 'Aditya Sonawane'


def connect_to_db():
    try:
        connection = connector.connect(**config.mysql_credentials)
        return connection
    except connector.Error as e:
        print(f"Error connecting to database: {e}")
        return None


# ==============================================================
# AUTHENTICATION & ACCESS CONTROL
# ==============================================================

def admin_required(f):
    """
    Decorator to restrict access to administrator users only.
    Checks session and verifies is_admin status in the database.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            flash("Please log in as an administrator to access this page.", "error")
            return redirect(url_for('login'))

        connection = connect_to_db()
        if not connection:
            flash("Database connection failed. Please try again later.", "error")
            return redirect(url_for('home'))

        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT id, is_admin, name FROM user_info WHERE email = %s",
                    (session['user_email'],)
                )
                user = cursor.fetchone()
                if not user or not user.get('is_admin'):
                    flash("Access denied: Administrator privileges required.", "error")
                    return redirect(url_for('dashboard'))

                session['is_admin'] = True
                if user.get('name'):
                    session['user_name'] = user['name']
        except connector.Error as e:
            print(f"Admin check error: {e}")
            flash("An error occurred while verifying privileges.", "error")
            return redirect(url_for('dashboard'))
        finally:
            connection.close()

        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name')
        password = request.form.get('password')
        email = request.form.get('email')
        vehicle_id = request.form.get('vehicleId')
        contact_number = request.form.get('phoneNumber')
        address = request.form.get('address')
        car_brand = request.form.get('carBrand')
        model = request.form.get('carModel')
        insurance_provider = request.form.get('insuranceProvider', 'Royal Sundaram General Insurance')
        policy_number = request.form.get('policyNumber', f'POL-2026-{int(datetime.now().timestamp()) % 1000000}')
        policy_type = request.form.get('policyType', 'Standard')

        if not all([name, password, email, vehicle_id, contact_number, address, car_brand, model]):
            flash("All fields are required!", "error")
            return render_template('signup.html')

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        connection = connect_to_db()
        if connection:
            try:
                with connection.cursor() as cursor:
                    query = '''
                    INSERT INTO user_info (
                        name, password, email, vehicle_id, contact_number, 
                        address, car_brand, model, is_admin,
                        insurance_provider, policy_number, policy_type
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s)
                    '''
                    cursor.execute(query, (
                        name, hashed_password, email, vehicle_id, contact_number, 
                        address, car_brand, model, insurance_provider, policy_number, policy_type
                    ))
                    connection.commit()
                flash("Signup successful! Please log in.", "success")
                return redirect(url_for('login'))
            except connector.IntegrityError as e:
                if 'Duplicate entry' in str(e):
                    flash("Email already exists. Please use a different email.", "error")
                else:
                    flash("An error occurred while signing up. Please try again.", "error")
            except connector.Error as e:
                print(f"Error executing query: {e}")
                flash("An error occurred while signing up. Please try again.", "error")
            finally:
                connection.close()
        else:
            flash("Database connection failed. Please try again later.", "error")
            
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash("Email and password are required!", "error")
            return render_template('login.html')

        connection = connect_to_db()
        if connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    query = "SELECT password, is_admin, name FROM user_info WHERE email = %s"
                    cursor.execute(query, (email,))
                    result = cursor.fetchone()
                    if result and bcrypt.checkpw(password.encode('utf-8'), result['password'].encode('utf-8')):
                        session['user_email'] = email
                        session['user_name'] = result.get('name', 'User')
                        session['is_admin'] = bool(result.get('is_admin', 0))
                        flash("Login successful!", "success")
                        if session['is_admin']:
                            return redirect(url_for('admin_dashboard'))
                        return redirect(url_for('dashboard'))
                    else:
                        flash("Invalid email or password.", "error")
            except connector.Error as e:
                print(f"Error executing query: {e}")
                flash("An error occurred during login. Please try again.", "error")
            finally:
                connection.close()
        else:
            flash("Database connection failed. Please try again later.", "error")

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('user_name', None)
    session.pop('is_admin', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))


# ==============================================================
# YOLO MODEL & USER DAMAGE DETECTION DASHBOARD
# ==============================================================

model_path = "models/model weights/best.pt"
model = YOLO(model_path)


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if request.method == 'POST':
        file = request.files.get('image')
        if not file:
            flash('Please upload an image.', 'error')
            return render_template('dashboard.html')

        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            flash('Invalid file type. Please upload a valid image (.jpg, .jpeg, .png).', 'error')
            return render_template('dashboard.html')
        
        # Create uploads directory
        upload_dir = os.path.join('static', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Unique timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        
        # Save original image
        original_filename = f"uploaded_{timestamp}.jpg"
        image_path = os.path.join(upload_dir, original_filename)
        file.save(image_path)
        
        # Run YOLO detection
        result = model(image_path)
        
        # Save detected visual output
        detected_filename = f"detected_{timestamp}.jpg"
        detected_path = os.path.join(upload_dir, detected_filename)
        result[0].save(detected_path)
        
        user_email = session.get('user_email')
        if not user_email:
            flash('Please log in again to save and view your estimate.', 'error')
            return redirect(url_for('login'))

        # Classify severity using utils/severity.py
        classified_detections = extract_and_classify_detections(result[0])
        
        connection = connect_to_db()
        car_brand = 'Generic'
        car_model = 'Standard'
        user_id = None
        report_id = None
        triage_info = {}
        
        if connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT id, name, car_brand, model, is_admin FROM user_info WHERE email = %s", (user_email,))
                    user_data = cursor.fetchone()
                    if user_data:
                        user_id = user_data.get('id')
                        car_brand = user_data.get('car_brand', 'Generic')
                        car_model = user_data.get('model', 'Standard')
                        session['is_admin'] = bool(user_data.get('is_admin', 0))
            except Exception as e:
                print(f"Error loading user profile: {e}")

        # Calculate pricing with severity multipliers
        pricing_data = calculate_pricing_with_severity(
            classified_detections=classified_detections,
            car_brand=car_brand,
            car_model=car_model,
            db_connection=connection,
            default_price=200.0
        )
        
        original_for_template = f"uploads/{original_filename}"
        detected_for_template = f"uploads/{detected_filename}"
        
        # Store report, detections, and triage scoring in MySQL
        if connection:
            try:
                with connection.cursor() as cursor:
                    # 1. Insert into damage_reports
                    cursor.execute('''
                        INSERT INTO damage_reports 
                        (user_id, user_email, original_image, detected_image, car_brand, car_model, total_cost)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        user_id,
                        user_email,
                        original_for_template,
                        detected_for_template,
                        car_brand,
                        car_model,
                        pricing_data['total_cost']
                    ))
                    report_id = cursor.lastrowid
                    
                    # 2. Insert each detection into damage_detections
                    for item in pricing_data['items']:
                        cursor.execute('''
                            INSERT INTO damage_detections 
                            (report_id, part_name, severity, confidence, bbox_area_ratio, overlap_count, base_price, multiplier, final_price)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (
                            report_id,
                            item['part_name'],
                            item['severity'],
                            item['confidence'],
                            item['area_ratio'],
                            item['overlap_count'],
                            item['base_price'],
                            item['multiplier'],
                            item['final_price']
                        ))
                    connection.commit()

                # 3. Synchronize triage score and priority queue
                triage_info = evaluate_and_update_report_triage(connection, report_id)

            except connector.Error as e:
                print(f"Error persisting damage report and triage to MySQL: {e}")
            finally:
                connection.close()

        return render_template(
            'estimate.html',
            report_id=report_id,
            triage_info=triage_info,
            original_image=original_for_template,
            detected_image=detected_for_template,
            detection_items=pricing_data['items'],
            part_prices=pricing_data['summary_by_part'],
            total_cost=pricing_data['total_cost'],
            severity_counts=pricing_data['severity_counts']
        )
    
    return render_template('dashboard.html')


@app.route('/view_profile')
def view_profile():
    if 'user_email' not in session:
        flash('You need to login to view your profile.', 'error')
        return redirect(url_for('login'))

    connection = connect_to_db()
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM user_info WHERE email = %s", (session['user_email'],))
                user_info = cursor.fetchone()
                if not user_info:
                    flash('User not found.', 'error')
                    return redirect(url_for('dashboard'))
                return render_template('view_profile.html', user_info=user_info)
        except connector.Error as e:
            print(f"Error executing query: {e}")
            flash("An error occurred while fetching your profile.", "error")
        finally:
            connection.close()
    else:
        flash("Database connection failed. Please try again later.", "error")

    return redirect(url_for('dashboard'))


@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_email' not in session:
        flash('You need to login to edit your profile.', 'error')
        return redirect(url_for('login'))

    connection = connect_to_db()
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                if request.method == 'POST':
                    query = '''
                    UPDATE user_info
                    SET name = %s, email = %s, vehicle_id = %s, contact_number = %s, 
                        address = %s, car_brand = %s, model = %s,
                        insurance_provider = %s, policy_number = %s, policy_type = %s
                    WHERE email = %s
                    '''
                    cursor.execute(query, (
                        request.form['name'],
                        request.form['email'],
                        request.form['vehicleId'],
                        request.form['phoneNumber'],
                        request.form['address'],
                        request.form['carBrand'],
                        request.form['carModel'],
                        request.form.get('insurance_provider', 'Royal Sundaram General Insurance'),
                        request.form.get('policy_number', 'POL-2026-889901'),
                        request.form.get('policy_type', 'Standard'),
                        session['user_email']
                    ))
                    connection.commit()
                    flash('Profile updated successfully!', 'success')
                    session['user_email'] = request.form['email']
                    session['user_name'] = request.form['name']
                    return redirect(url_for('dashboard'))

                cursor.execute("SELECT * FROM user_info WHERE email = %s", (session['user_email'],))
                user_info = cursor.fetchone()
                return render_template('edit_profile.html', user_info=user_info)
        except connector.Error as e:
            print(f"Error executing query: {e}")
            flash("An error occurred while updating your profile.", "error")
        finally:
            connection.close()
    else:
        flash("Database connection failed. Please try again later.", "error")

    return redirect(url_for('dashboard'))


# ==============================================================
# FEATURE A: INSURANCE CLAIM ASSISTANT ROUTES
# ==============================================================

@app.route('/claim/start')
def claim_start():
    if 'user_email' not in session:
        flash('Please login to file an insurance claim.', 'error')
        return redirect(url_for('login'))

    report_id = request.args.get('report_id')
    connection = connect_to_db()
    user_info = {}
    report = None
    available_reports = []

    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                # 1. Fetch user policy info
                cursor.execute("SELECT * FROM user_info WHERE email = %s", (session['user_email'],))
                user_info = cursor.fetchone() or {}

                # 2. Fetch available user damage reports
                cursor.execute("""
                    SELECT id, car_brand, car_model, total_cost, original_image, detected_image, created_at
                    FROM damage_reports
                    WHERE user_email = %s
                    ORDER BY id DESC
                """, (session['user_email'],))
                available_reports = cursor.fetchall()

                # If specific report_id requested
                if report_id:
                    cursor.execute("SELECT * FROM damage_reports WHERE id = %s", (report_id,))
                    report = cursor.fetchone()
                elif available_reports:
                    report = available_reports[0]

        except connector.Error as e:
            print(f"Claim start error: {e}")
        finally:
            connection.close()

    return render_template(
        'insurance/start_claim.html',
        user_info=user_info,
        report=report,
        available_reports=available_reports
    )


@app.route('/claim/submit', methods=['POST'])
def claim_submit():
    if 'user_email' not in session:
        flash('Please log in to submit your insurance claim.', 'error')
        return redirect(url_for('login'))

    user_email = session['user_email']
    report_id = request.form.get('report_id') or request.form.get('report_id_select')
    policy_number = request.form.get('policy_number', 'POL-2026-889901')
    policy_type = request.form.get('policy_type', 'Standard')
    incident_description = request.form.get('incident_description', '')
    incident_date = request.form.get('incident_date', datetime.now().strftime('%Y-%m-%d'))
    incident_location = request.form.get('incident_location', 'N/A')

    if not report_id:
        flash('Please select a valid damage assessment report to link with your claim.', 'error')
        return redirect(url_for('claim_start'))

    connection = connect_to_db()
    if not connection:
        flash('Database connection failed. Please try again.', 'error')
        return redirect(url_for('claim_start'))

    try:
        with connection.cursor(dictionary=True) as cursor:
            # 1. Fetch report details & detections
            cursor.execute("SELECT * FROM damage_reports WHERE id = %s", (report_id,))
            report = cursor.fetchone()
            if not report:
                flash('Selected damage report not found.', 'error')
                return redirect(url_for('claim_start'))

            cursor.execute("SELECT severity FROM damage_detections WHERE report_id = %s", (report_id,))
            severities = [d['severity'] for d in cursor.fetchall()]

            cursor.execute("SELECT id FROM user_info WHERE email = %s", (user_email,))
            u = cursor.fetchone()
            user_id = u['id'] if u else None

        # Create claim with auto-approval check and timeline initialization
        claim_result = create_claim_record(
            db_connection=connection,
            user_id=user_id,
            user_email=user_email,
            report_id=int(report_id),
            policy_number=policy_number,
            policy_type=policy_type,
            total_cost=float(report['total_cost']),
            incident_description=incident_description,
            incident_date=incident_date,
            incident_location=incident_location,
            severities=severities
        )

        if claim_result['is_auto_approved']:
            flash(f"⚡ Fast-Track Auto-Approval Granted! Claim {claim_result['claim_number']} approved for ₹{claim_result['covered_amount']:,.2f}.", "success")
        else:
            flash(f"Claim {claim_result['claim_number']} submitted successfully! Assigned for adjuster review.", "info")

        return redirect(url_for('claim_status'))

    except Exception as e:
        print(f"Error submitting claim: {e}")
        flash("An error occurred while filing your claim. Please try again.", "error")
        return redirect(url_for('claim_start'))
    finally:
        connection.close()


@app.route('/claim/status')
def claim_status():
    if 'user_email' not in session:
        flash('Please log in to track your insurance claims.', 'error')
        return redirect(url_for('login'))

    connection = connect_to_db()
    claims = []

    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT * FROM insurance_claims 
                    WHERE user_email = %s 
                    ORDER BY id DESC
                """, (session['user_email'],))
                claims = cursor.fetchall()

                # Attach timeline events to each claim
                for c in claims:
                    cursor.execute("""
                        SELECT * FROM claim_timeline 
                        WHERE claim_id = %s 
                        ORDER BY id ASC
                    """, (c['id'],))
                    c['timeline'] = cursor.fetchall()
        except connector.Error as e:
            print(f"Claim status fetch error: {e}")
        finally:
            connection.close()

    return render_template('insurance/claim_status.html', claims=claims)


@app.route('/claim/<claim_number>/pdf')
def claim_download_pdf(claim_number):
    """
    Generate and stream official PDF claim assessment report using ReportLab.
    """
    connection = connect_to_db()
    if not connection:
        return "Database connection failed", 500

    try:
        with connection.cursor(dictionary=True) as cursor:
            # 1. Fetch claim & associated report
            cursor.execute("""
                SELECT c.*, r.car_brand, r.car_model, r.original_image, r.detected_image
                FROM insurance_claims c
                LEFT JOIN damage_reports r ON c.report_id = r.id
                WHERE c.claim_number = %s
            """, (claim_number,))
            claim = cursor.fetchone()
            if not claim:
                return "Claim not found", 404

            # 2. Fetch user information
            cursor.execute("SELECT * FROM user_info WHERE email = %s", (claim['user_email'],))
            user_info = cursor.fetchone() or {}

            # 3. Fetch detections
            cursor.execute("""
                SELECT part_name, severity, confidence, base_price, multiplier, final_price
                FROM damage_detections
                WHERE report_id = %s
            """, (claim['report_id'],))
            detections = cursor.fetchall()

        pdf_bytes = generate_claim_pdf(claim, detections, user_info)

        filename = f"Insurance_Claim_{claim_number}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"inline; filename={filename}"}
        )
    except Exception as e:
        print(f"PDF Generation Error: {e}")
        return f"Error generating claim PDF: {e}", 500
    finally:
        connection.close()


@app.route('/admin/claims')
@admin_required
def admin_claims():
    connection = connect_to_db()
    claims = []
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT c.*, u.name as user_name
                    FROM insurance_claims c
                    LEFT JOIN user_info u ON c.user_id = u.id
                    ORDER BY c.id DESC
                """)
                claims = cursor.fetchall()
        except connector.Error as e:
            print(f"Admin claims fetch error: {e}")
        finally:
            connection.close()

    return render_template('admin/claims.html', claims=claims, active_page='claims')


@app.route('/admin/claims/<int:claim_id>/update-status', methods=['POST'])
@admin_required
def admin_update_claim_status(claim_id):
    new_status = request.form.get('status')
    admin_notes = request.form.get('admin_notes', f"Status updated to {new_status} by administrator.")

    connection = connect_to_db()
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    UPDATE insurance_claims 
                    SET status = %s, admin_notes = %s 
                    WHERE id = %s
                """, (new_status, admin_notes, claim_id))

                # Append timeline entry
                cursor.execute("""
                    INSERT INTO claim_timeline (claim_id, status, title, description, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    claim_id,
                    new_status,
                    f"Claim Status Updated: {new_status}",
                    admin_notes,
                    session.get('user_name', 'Insurance Claims Administrator')
                ))
                connection.commit()
                flash(f"Claim #{claim_id} status updated to {new_status}.", "success")
        except connector.Error as e:
            print(f"Update claim status error: {e}")
            flash("Error updating claim status.", "error")
        finally:
            connection.close()

    return redirect(url_for('admin_claims'))


# ==============================================================
# FEATURE B: DAMAGE TRIAGE & PRIORITIZATION ROUTES
# ==============================================================

@app.route('/admin/triage')
@admin_required
def admin_triage():
    connection = connect_to_db()
    triage_items = []
    stats = {
        'critical_count': 0,
        'high_count': 0,
        'medium_count': 0,
        'low_count': 0,
        'breached_count': 0,
        'compliant_count': 0,
        'avg_score': 0
    }
    chart_tech = {'labels': [], 'data': []}

    if connection:
        try:
            # 1. Refresh SLA breach status
            refresh_sla_status_all(connection)

            with connection.cursor(dictionary=True) as cursor:
                # 2. Fetch triage queue items
                cursor.execute("""
                    SELECT q.*, r.car_brand, r.car_model, r.total_cost, r.original_image, r.detected_image
                    FROM triage_queue q
                    LEFT JOIN damage_reports r ON q.report_id = r.id
                    ORDER BY q.triage_score DESC, q.created_at DESC
                """)
                triage_items = cursor.fetchall()

                # 3. Statistics
                for item in triage_items:
                    p = item['priority']
                    if p == 'Critical': stats['critical_count'] += 1
                    elif p == 'High': stats['high_count'] += 1
                    elif p == 'Medium': stats['medium_count'] += 1
                    else: stats['low_count'] += 1

                    if item['sla_breached']:
                        stats['breached_count'] += 1
                    else:
                        stats['compliant_count'] += 1

                if triage_items:
                    stats['avg_score'] = int(round(sum(i['triage_score'] for i in triage_items) / len(triage_items)))

                # 4. Workload by technician chart
                cursor.execute("""
                    SELECT assigned_to, COUNT(*) as count
                    FROM triage_queue
                    GROUP BY assigned_to
                    ORDER BY count DESC
                """)
                tech_rows = cursor.fetchall()
                chart_tech['labels'] = [t['assigned_to'] for t in tech_rows]
                chart_tech['data'] = [t['count'] for t in tech_rows]

        except connector.Error as e:
            print(f"Error fetching triage queue: {e}")
        finally:
            connection.close()

    return render_template(
        'admin/triage.html',
        triage_items=triage_items,
        stats=stats,
        chart_tech=chart_tech,
        active_page='triage'
    )


@app.route('/admin/triage/<int:report_id>')
@admin_required
def admin_triage_detail(report_id):
    connection = connect_to_db()
    if not connection:
        flash("Database connection failed.", "error")
        return redirect(url_for('admin_triage'))

    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT * FROM damage_reports WHERE id = %s", (report_id,))
            report = cursor.fetchone()
            if not report:
                flash("Report not found.", "error")
                return redirect(url_for('admin_triage'))

            cursor.execute("SELECT * FROM triage_queue WHERE report_id = %s", (report_id,))
            queue_item = cursor.fetchone()

            cursor.execute("""
                SELECT part_name, severity, confidence, base_price, multiplier, final_price
                FROM damage_detections
                WHERE report_id = %s
            """, (report_id,))
            detections = cursor.fetchall()
            for d in detections:
                d['badge_class'] = SEVERITY_BADGE_CLASSES.get(d['severity'], 'badge-minor')

        triage_calc = calculate_triage_score(
            severities=[d['severity'] for d in detections],
            parts_count=len(detections),
            total_cost=float(report['total_cost']),
            days_pending=0
        )

        return render_template(
            'admin/triage_detail.html',
            report=report,
            queue_item=queue_item or {},
            detections=detections,
            triage_calc=triage_calc,
            active_page='triage'
        )
    finally:
        connection.close()


@app.route('/admin/triage/<int:report_id>/update', methods=['POST'])
@admin_required
def admin_triage_update(report_id):
    assigned_to = request.form.get('assigned_to')
    new_status = request.form.get('status')
    priority = request.form.get('priority')
    notes = request.form.get('notes')

    tech_role = PRIORITY_CONFIG.get(priority, {}).get('role', 'Certified Collision Specialist')

    connection = connect_to_db()
    if connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE triage_queue
                    SET assigned_to = %s,
                        technician_role = %s,
                        status = %s,
                        priority = %s,
                        notes = %s
                    WHERE report_id = %s
                """, (assigned_to, tech_role, new_status, priority, notes, report_id))

                cursor.execute("""
                    UPDATE damage_reports
                    SET assigned_to = %s,
                        technician_role = %s,
                        triage_status = %s,
                        priority = %s
                    WHERE id = %s
                """, (assigned_to, tech_role, new_status, priority, report_id))
                connection.commit()
                flash(f"Triage assignment for Report #{report_id} updated successfully.", "success")
        except connector.Error as e:
            print(f"Error updating triage: {e}")
            flash("Error updating triage assignment.", "error")
        finally:
            connection.close()

    return redirect(url_for('admin_triage'))


@app.route('/triage/queue')
def triage_queue_view():
    """
    Staff / workshop technician view of active triage work orders.
    """
    connection = connect_to_db()
    items = []
    if connection:
        try:
            refresh_sla_status_all(connection)
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT q.*, r.car_brand, r.car_model, r.total_cost
                    FROM triage_queue q
                    LEFT JOIN damage_reports r ON q.report_id = r.id
                    ORDER BY q.triage_score DESC, q.created_at DESC
                """)
                items = cursor.fetchall()
        except connector.Error as e:
            print(f"Triage queue fetch error: {e}")
        finally:
            connection.close()

    return render_template('triage/queue.html', items=items)


@app.route('/triage/queue/<int:report_id>/advance', methods=['POST'])
def triage_advance_status(report_id):
    new_status = request.form.get('new_status', 'In Progress')
    connection = connect_to_db()
    if connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE triage_queue SET status = %s WHERE report_id = %s
                """, (new_status, report_id))
                cursor.execute("""
                    UPDATE damage_reports SET triage_status = %s WHERE id = %s
                """, (new_status, report_id))
                connection.commit()
                flash(f"Work order #{report_id} advanced to '{new_status}'.", "success")
        except connector.Error as e:
            print(f"Advance status error: {e}")
        finally:
            connection.close()

    return redirect(url_for('triage_queue_view'))


# ==============================================================
# ADMIN PORTAL & ANALYTICS ROUTES
# ==============================================================

@app.route('/admin')
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    connection = connect_to_db()
    stats = {
        'total_reports': 0,
        'total_cost': 0.0,
        'total_users': 0,
        'admin_count': 0,
        'severe_count': 0,
        'recent_week_reports': 0,
        'avg_report_cost': 0.0,
        'severe_percentage': 0.0
    }
    recent_reports = []
    chart_daily = {'labels': [], 'data': []}
    chart_severity = {'data': {'Minor': 0, 'Moderate': 0, 'Severe': 0}}

    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                # 1. Total reports and cost
                cursor.execute("SELECT COUNT(*) as count, COALESCE(SUM(total_cost), 0) as total_cost FROM damage_reports")
                r_stats = cursor.fetchone()
                stats['total_reports'] = r_stats['count']
                stats['total_cost'] = float(r_stats['total_cost'])
                if stats['total_reports'] > 0:
                    stats['avg_report_cost'] = round(stats['total_cost'] / stats['total_reports'], 2)

                # 2. Total users & admins
                cursor.execute("SELECT COUNT(*) as total_users, SUM(CASE WHEN is_admin = 1 THEN 1 ELSE 0 END) as admin_count FROM user_info")
                u_stats = cursor.fetchone()
                stats['total_users'] = u_stats['total_users'] or 0
                stats['admin_count'] = u_stats['admin_count'] or 0

                # 3. Severe damage detections count
                cursor.execute("SELECT COUNT(*) as count FROM damage_detections WHERE severity = 'Severe'")
                stats['severe_count'] = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM damage_detections")
                total_dets = cursor.fetchone()['count']
                if total_dets > 0:
                    stats['severe_percentage'] = round((stats['severe_count'] / total_dets) * 100, 1)

                # 4. Reports this week
                cursor.execute("SELECT COUNT(*) as count FROM damage_reports WHERE created_at >= NOW() - INTERVAL 7 DAY")
                stats['recent_week_reports'] = cursor.fetchone()['count']

                # 5. Recent Reports list
                cursor.execute("""
                    SELECT r.id, r.user_email, r.original_image, r.detected_image, 
                           r.car_brand, r.car_model, r.total_cost, r.created_at,
                           u.name as user_name
                    FROM damage_reports r
                    LEFT JOIN user_info u ON r.user_id = u.id
                    ORDER BY r.created_at DESC
                    LIMIT 8
                """)
                recent_reports = cursor.fetchall()

                # Attach detection summaries to recent reports
                for rep in recent_reports:
                    cursor.execute("""
                        SELECT part_name, severity
                        FROM damage_detections
                        WHERE report_id = %s
                    """, (rep['id'],))
                    dets = cursor.fetchall()
                    for d in dets:
                        d['badge_class'] = SEVERITY_BADGE_CLASSES.get(d['severity'], 'badge-minor')
                    rep['parts_summary'] = dets

                # 6. Daily Uploads (past 10 days)
                cursor.execute("""
                    SELECT DATE_FORMAT(created_at, '%b %d') as date_label, COUNT(*) as count
                    FROM damage_reports
                    WHERE created_at >= NOW() - INTERVAL 14 DAY
                    GROUP BY DATE(created_at), date_label
                    ORDER BY DATE(created_at) ASC
                """)
                daily_rows = cursor.fetchall()
                if daily_rows:
                    chart_daily['labels'] = [row['date_label'] for row in daily_rows]
                    chart_daily['data'] = [row['count'] for row in daily_rows]
                else:
                    today = datetime.now()
                    chart_daily['labels'] = [(today - timedelta(days=i)).strftime('%b %d') for i in reversed(range(5))]
                    chart_daily['data'] = [0] * 5

                # 7. Severity breakdown for donut chart
                cursor.execute("""
                    SELECT severity, COUNT(*) as count
                    FROM damage_detections
                    GROUP BY severity
                """)
                sev_rows = cursor.fetchall()
                for s in sev_rows:
                    if s['severity'] in chart_severity['data']:
                        chart_severity['data'][s['severity']] = s['count']

        except connector.Error as e:
            print(f"Error fetching admin dashboard data: {e}")
            flash("Error loading administrative statistics.", "error")
        finally:
            connection.close()

    return render_template(
        'admin/dashboard.html',
        stats=stats,
        recent_reports=recent_reports,
        chart_daily=chart_daily,
        chart_severity=chart_severity,
        active_page='dashboard'
    )


@app.route('/admin/users')
@admin_required
def admin_users():
    connection = connect_to_db()
    users = []
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT id, name, email, vehicle_id, contact_number, 
                           address, car_brand, model, is_admin, registration_date
                    FROM user_info
                    ORDER BY id DESC
                """)
                users = cursor.fetchall()
        except connector.Error as e:
            print(f"Error loading users: {e}")
            flash("Error loading user directory.", "error")
        finally:
            connection.close()

    return render_template('admin/users.html', users=users, active_page='users')


@app.route('/admin/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def admin_toggle_user_role(user_id):
    connection = connect_to_db()
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT email, is_admin FROM user_info WHERE id = %s", (user_id,))
                user = cursor.fetchone()
                if not user:
                    flash("User not found.", "error")
                    return redirect(url_for('admin_users'))

                if user['email'] == session.get('user_email'):
                    flash("You cannot modify your own administrator status.", "error")
                    return redirect(url_for('admin_users'))

                new_role = not bool(user['is_admin'])
                cursor.execute("UPDATE user_info SET is_admin = %s WHERE id = %s", (new_role, user_id))
                connection.commit()
                flash(f"User role updated successfully for {user['email']}.", "success")
        except connector.Error as e:
            print(f"Error updating user admin status: {e}")
            flash("Error updating user status.", "error")
        finally:
            connection.close()

    return redirect(url_for('admin_users'))


@app.route('/admin/reports')
@admin_required
def admin_reports():
    connection = connect_to_db()
    reports = []
    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT r.id, r.user_id, r.user_email, r.original_image, r.detected_image,
                           r.car_brand, r.car_model, r.total_cost, r.created_at,
                           u.name as user_name, u.vehicle_id
                    FROM damage_reports r
                    LEFT JOIN user_info u ON r.user_id = u.id
                    ORDER BY r.created_at DESC
                """)
                reports = cursor.fetchall()

                # Attach detection items to each report
                for rep in reports:
                    cursor.execute("""
                        SELECT part_name, severity, confidence, base_price, multiplier, final_price
                        FROM damage_detections
                        WHERE report_id = %s
                    """, (rep['id'],))
                    dets = cursor.fetchall()
                    for d in dets:
                        d['badge_class'] = SEVERITY_BADGE_CLASSES.get(d['severity'], 'badge-minor')
                    rep['detections'] = dets

        except connector.Error as e:
            print(f"Error fetching reports: {e}")
            flash("Error fetching damage assessment reports.", "error")
        finally:
            connection.close()

    return render_template('admin/reports.html', reports=reports, active_page='reports')


@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    connection = connect_to_db()
    chart_daily = {'labels': [], 'data': []}
    chart_top_parts = {'labels': [], 'data': []}
    chart_severity = {'data': {'Minor': 0, 'Moderate': 0, 'Severe': 0}}
    chart_avg_cost = {'labels': [], 'data': []}

    analytics_summary = {
        'total_detections': 0,
        'total_reports': 0,
        'most_damaged_part': 'None',
        'most_damaged_count': 0,
        'overall_avg_part_cost': 0.0,
        'avg_multiplier': 1.0
    }

    if connection:
        try:
            with connection.cursor(dictionary=True) as cursor:
                # 1. Total counts
                cursor.execute("SELECT COUNT(*) as count FROM damage_reports")
                analytics_summary['total_reports'] = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count, AVG(final_price) as avg_price, AVG(multiplier) as avg_mult FROM damage_detections")
                det_summary = cursor.fetchone()
                analytics_summary['total_detections'] = det_summary['count'] or 0
                analytics_summary['overall_avg_part_cost'] = round(float(det_summary['avg_price'] or 0), 2)
                analytics_summary['avg_multiplier'] = round(float(det_summary['avg_mult'] or 1.0), 2)

                # 2. Daily uploads trend
                cursor.execute("""
                    SELECT DATE_FORMAT(created_at, '%b %d') as date_label, COUNT(*) as count
                    FROM damage_reports
                    WHERE created_at >= NOW() - INTERVAL 14 DAY
                    GROUP BY DATE(created_at), date_label
                    ORDER BY DATE(created_at) ASC
                """)
                daily_rows = cursor.fetchall()
                if daily_rows:
                    chart_daily['labels'] = [r['date_label'] for r in daily_rows]
                    chart_daily['data'] = [r['count'] for r in daily_rows]
                else:
                    today = datetime.now()
                    chart_daily['labels'] = [(today - timedelta(days=i)).strftime('%b %d') for i in reversed(range(7))]
                    chart_daily['data'] = [0] * 7

                # 3. Top Damaged Parts
                cursor.execute("""
                    SELECT part_name, COUNT(*) as count
                    FROM damage_detections
                    GROUP BY part_name
                    ORDER BY count DESC
                    LIMIT 7
                """)
                parts_rows = cursor.fetchall()
                if parts_rows:
                    chart_top_parts['labels'] = [r['part_name'] for r in parts_rows]
                    chart_top_parts['data'] = [r['count'] for r in parts_rows]
                    analytics_summary['most_damaged_part'] = parts_rows[0]['part_name']
                    analytics_summary['most_damaged_count'] = parts_rows[0]['count']

                # 4. Severity Distribution (Pie)
                cursor.execute("""
                    SELECT severity, COUNT(*) as count
                    FROM damage_detections
                    GROUP BY severity
                """)
                for s in cursor.fetchall():
                    if s['severity'] in chart_severity['data']:
                        chart_severity['data'][s['severity']] = s['count']

                # 5. Average Cost per Part
                cursor.execute("""
                    SELECT part_name, ROUND(AVG(final_price), 2) as avg_cost
                    FROM damage_detections
                    GROUP BY part_name
                    ORDER BY avg_cost DESC
                    LIMIT 7
                """)
                cost_rows = cursor.fetchall()
                chart_avg_cost['labels'] = [r['part_name'] for r in cost_rows]
                chart_avg_cost['data'] = [float(r['avg_cost']) for r in cost_rows]

        except connector.Error as e:
            print(f"Error generating analytics: {e}")
            flash("Error compiling analytics.", "error")
        finally:
            connection.close()

    return render_template(
        'admin/analytics.html',
        chart_daily=chart_daily,
        chart_top_parts=chart_top_parts,
        chart_severity=chart_severity,
        chart_avg_cost=chart_avg_cost,
        analytics_summary=analytics_summary,
        active_page='analytics'
    )


@app.route('/admin/export-csv')
@app.route('/admin/reports/export-csv')
@admin_required
def admin_export_csv():
    """
    Export all vehicle damage reports and itemized detections to a CSV file.
    """
    connection = connect_to_db()
    if not connection:
        flash("Database connection failed for CSV export.", "error")
        return redirect(url_for('admin_reports'))

    output = io.StringIO()
    writer = csv.writer(output)

    # Write CSV Header
    writer.writerow([
        'Report ID',
        'Date & Time',
        'User Name',
        'User Email',
        'Vehicle ID',
        'Car Brand',
        'Car Model',
        'Total Cost (₹)',
        'Parts Count',
        'Detected Parts & Severities'
    ])

    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT r.id, r.user_email, r.car_brand, r.car_model, r.total_cost, r.created_at,
                       u.name as user_name, u.vehicle_id
                FROM damage_reports r
                LEFT JOIN user_info u ON r.user_id = u.id
                ORDER BY r.id DESC
            """)
            reports = cursor.fetchall()

            for rep in reports:
                cursor.execute("""
                    SELECT part_name, severity, multiplier, final_price
                    FROM damage_detections
                    WHERE report_id = %s
                """, (rep['id'],))
                dets = cursor.fetchall()
                parts_str = "; ".join([f"{d['part_name']} ({d['severity']}, {d['multiplier']}x, ₹{d['final_price']})" for d in dets])

                writer.writerow([
                    rep['id'],
                    rep['created_at'].strftime('%Y-%m-%d %H:%M:%S') if rep['created_at'] else '',
                    rep['user_name'] or 'N/A',
                    rep['user_email'],
                    rep['vehicle_id'] or 'N/A',
                    rep['car_brand'] or '',
                    rep['car_model'] or '',
                    f"{float(rep['total_cost']):.2f}",
                    len(dets),
                    parts_str
                ])
    except connector.Error as e:
        print(f"CSV Export DB error: {e}")
        flash("Error exporting reports to CSV.", "error")
        return redirect(url_for('admin_reports'))
    finally:
        connection.close()

    csv_data = output.getvalue()
    filename = f"vehicle_damage_reports_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ==============================================================
# ENTRY POINT
# ==============================================================

if __name__ == '__main__':
    app.run(debug=True)