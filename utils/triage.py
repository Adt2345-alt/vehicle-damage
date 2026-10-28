"""
Damage Triage & Prioritization Engine
Vehicle Damage Detection System
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple


# Prioritization Thresholds as specified
# Priority: Critical (75+), High (50-74), Medium (25-49), Low (<25)
PRIORITY_THRESHOLDS = {
    'Critical': 75,
    'High': 50,
    'Medium': 25,
    'Low': 0
}

PRIORITY_CONFIG = {
    'Critical': {
        'technician': 'Senior Master Tech - John Doe',
        'role': 'Chief Collision & Structural Specialist',
        'sla_hours': 6,
        'badge_class': 'badge-severe',
        'color': '#EF4444'
    },
    'High': {
        'technician': 'Senior Bodywork Specialist - Sarah Connor',
        'role': 'Senior Panel & Alignment Lead',
        'sla_hours': 12,
        'badge_class': 'badge-moderate',
        'color': '#F59E0B'
    },
    'Medium': {
        'technician': 'Collision Tech - Marcus Vance',
        'role': 'Certified Body & Paint Technician',
        'sla_hours': 24,
        'badge_class': 'badge-admin',
        'color': '#6366F1'
    },
    'Low': {
        'technician': 'Junior Service Tech - Alex Chen',
        'role': 'Cosmetic & Surface Apprentice',
        'sla_hours': 48,
        'badge_class': 'badge-minor',
        'color': '#10B981'
    }
}


def calculate_triage_score(
    severities: List[str],
    parts_count: int,
    total_cost: float,
    days_pending: int = 0,
    user_type: str = 'Personal'
) -> Dict[str, Any]:
    """
    Compute damage triage score (0-100) based on:
      1. Severity of detected damage (up to 45 pts)
      2. Component count (up to 25 pts)
      3. Estimated financial cost (up to 20 pts)
      4. Age / Pending days & customer classification (up to 10 pts)
    """
    cleaned_sevs = [s.strip().capitalize() for s in severities if s]
    
    # 1. Severity points (Max 45)
    severity_pts = 0
    if any(s == 'Severe' for s in cleaned_sevs):
        severity_pts = 40
        # Bonus for compound severe parts
        severe_count = sum(1 for s in cleaned_sevs if s == 'Severe')
        if severe_count > 1:
            severity_pts = 45
    elif any(s == 'Moderate' for s in cleaned_sevs):
        severity_pts = 25
        mod_count = sum(1 for s in cleaned_sevs if s == 'Moderate')
        if mod_count > 1:
            severity_pts = 30
    elif any(s == 'Minor' for s in cleaned_sevs):
        severity_pts = 12
    else:
        severity_pts = 5

    # 2. Parts count points (Max 25)
    cnt = max(1, parts_count)
    if cnt >= 4:
        parts_pts = 25
    elif cnt == 3:
        parts_pts = 18
    elif cnt == 2:
        parts_pts = 12
    else:
        parts_pts = 6

    # 3. Cost factor points (Max 20)
    # Scale: ₹20,000+ -> 20 pts
    cost_pts = min(20.0, (float(total_cost) / 20000.0) * 20.0)

    # 4. Age & User Type points (Max 10)
    user_pts = 0
    norm_user = user_type.lower() if user_type else 'personal'
    if 'fleet' in norm_user or 'commercial' in norm_user:
        user_pts += 5
    elif 'vip' in norm_user or 'priority' in norm_user:
        user_pts += 6

    age_pts = min(4, days_pending * 2)
    meta_pts = min(10, user_pts + age_pts)

    # Total score clamped to 0 - 100
    raw_score = severity_pts + parts_pts + cost_pts + meta_pts
    final_score = int(round(max(0, min(100, raw_score))))

    # Priority mapping
    if final_score >= PRIORITY_THRESHOLDS['Critical']:
        priority = 'Critical'
    elif final_score >= PRIORITY_THRESHOLDS['High']:
        priority = 'High'
    elif final_score >= PRIORITY_THRESHOLDS['Medium']:
        priority = 'Medium'
    else:
        priority = 'Low'

    config = PRIORITY_CONFIG[priority]
    sla_hours = config['sla_hours']

    return {
        'score': final_score,
        'priority': priority,
        'assigned_to': config['technician'],
        'technician_role': config['role'],
        'sla_hours': sla_hours,
        'badge_class': config['badge_class'],
        'color': config['color'],
        'factors': {
            'severity_pts': severity_pts,
            'parts_pts': parts_pts,
            'cost_pts': round(cost_pts, 1),
            'meta_pts': meta_pts
        }
    }


def evaluate_and_update_report_triage(db_connection, report_id: int) -> Dict[str, Any]:
    """
    Evaluates triage score and SLA deadline for a damage report and synchronizes
    both `damage_reports` and `triage_queue` tables.
    """
    with db_connection.cursor(dictionary=True) as cursor:
        # Load report & user
        cursor.execute("""
            SELECT r.id, r.total_cost, r.created_at, u.user_type
            FROM damage_reports r
            LEFT JOIN user_info u ON r.user_id = u.id
            WHERE r.id = %s
        """, (report_id,))
        rep = cursor.fetchone()
        if not rep:
            return {}

        # Load detections
        cursor.execute("SELECT part_name, severity FROM damage_detections WHERE report_id = %s", (report_id,))
        detections = cursor.fetchall()
        severities = [d['severity'] for d in detections]
        parts_count = len(detections)
        total_cost = float(rep['total_cost'])
        user_type = rep.get('user_type') or 'Personal'

        # Calculate days pending
        created_time = rep['created_at'] if rep.get('created_at') else datetime.now()
        days_pending = max(0, (datetime.now() - created_time).days)

        triage_info = calculate_triage_score(
            severities=severities,
            parts_count=parts_count,
            total_cost=total_cost,
            days_pending=days_pending,
            user_type=user_type
        )

        sla_deadline = created_time + timedelta(hours=triage_info['sla_hours'])
        sla_breached = datetime.now() > sla_deadline

        # Severity breakdown summary string
        sev_str = ", ".join(f"{d['part_name']} ({d['severity']})" for d in detections)

        # 1. Update damage_reports
        cursor.execute("""
            UPDATE damage_reports
            SET triage_score = %s,
                priority = %s,
                assigned_to = %s,
                technician_role = %s,
                sla_hours = %s,
                sla_deadline = %s,
                sla_breached = %s
            WHERE id = %s
        """, (
            triage_info['score'],
            triage_info['priority'],
            triage_info['assigned_to'],
            triage_info['technician_role'],
            triage_info['sla_hours'],
            sla_deadline,
            sla_breached,
            report_id
        ))

        # 2. Insert or update triage_queue
        cursor.execute("""
            INSERT INTO triage_queue 
            (report_id, triage_score, priority, assigned_to, technician_role, sla_hours, sla_deadline, sla_breached, status, severity_breakdown)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Queued', %s)
            ON DUPLICATE KEY UPDATE
                triage_score = VALUES(triage_score),
                priority = VALUES(priority),
                assigned_to = VALUES(assigned_to),
                technician_role = VALUES(technician_role),
                sla_hours = VALUES(sla_hours),
                sla_deadline = VALUES(sla_deadline),
                sla_breached = VALUES(sla_breached),
                severity_breakdown = VALUES(severity_breakdown)
        """, (
            report_id,
            triage_info['score'],
            triage_info['priority'],
            triage_info['assigned_to'],
            triage_info['technician_role'],
            triage_info['sla_hours'],
            sla_deadline,
            sla_breached,
            sev_str
        ))

        db_connection.commit()

        triage_info['sla_deadline'] = sla_deadline
        triage_info['sla_breached'] = sla_breached
        return triage_info


def refresh_sla_status_all(db_connection):
    """
    Check all open triage queue items and flag any that exceeded their SLA deadline.
    """
    with db_connection.cursor() as cursor:
        cursor.execute("""
            UPDATE triage_queue
            SET sla_breached = TRUE
            WHERE status != 'Completed' 
              AND sla_deadline IS NOT NULL 
              AND sla_deadline < NOW()
        """)
        cursor.execute("""
            UPDATE damage_reports
            SET sla_breached = TRUE
            WHERE triage_status != 'Completed'
              AND sla_deadline IS NOT NULL 
              AND sla_deadline < NOW()
        """)
        db_connection.commit()
