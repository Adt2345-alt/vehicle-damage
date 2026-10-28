"""
Insurance Claim Assistant Utility
Vehicle Damage Detection System
"""

import os
import random
import string
from datetime import datetime
from typing import Dict, Any, List, Tuple
import io

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
)


# Policy coverage percentages
POLICY_COVERAGE = {
    'Basic': 60.0,      # 60% coverage
    'Standard': 80.0,   # 80% coverage
    'Premium': 90.0     # 90% coverage
}

CLAIM_STATUS_COLORS = {
    'Draft': '#9CA3AF',
    'Submitted': '#3B82F6',
    'Under Review': '#F59E0B',
    'Approved': '#10B981',
    'Rejected': '#EF4444'
}


def generate_claim_number() -> str:
    """Generate a unique human-readable claim reference number."""
    year = datetime.now().strftime('%Y')
    chars = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"CLM-{year}-{chars}"


def calculate_claim_coverage(total_cost: float, policy_type: str = 'Standard') -> Dict[str, Any]:
    """
    Calculate covered amount and out-of-pocket expenses based on policy type.
    """
    total = max(0.0, float(total_cost))
    norm_type = policy_type.capitalize() if policy_type else 'Standard'
    pct = POLICY_COVERAGE.get(norm_type, 80.0)

    covered_amount = round(total * (pct / 100.0), 2)
    out_of_pocket = round(total - covered_amount, 2)

    return {
        'policy_type': norm_type,
        'coverage_percentage': pct,
        'covered_amount': covered_amount,
        'out_of_pocket': out_of_pocket,
        'total_damage_cost': round(total, 2)
    }


def check_auto_approval(total_cost: float, severities: List[str]) -> Tuple[bool, str]:
    """
    Auto-approve criteria:
    - Total damage cost < 5000
    - Maximum damage severity across all detections is 'Minor' (no Moderate or Severe damage)
    """
    cost_ok = float(total_cost) < 5000.0
    # Clean severities
    cleaned_sevs = [s.strip().capitalize() for s in severities if s]
    
    # Check if any Moderate or Severe exists
    has_moderate_or_severe = any(s in ['Moderate', 'Severe'] for s in cleaned_sevs)
    all_minor = len(cleaned_sevs) == 0 or (all(s == 'Minor' for s in cleaned_sevs) and not has_moderate_or_severe)

    if cost_ok and all_minor:
        return True, "Auto-Approved by System AI: Claim cost is under ₹5,000 and all damage is classified as Minor."
    elif not cost_ok and not all_minor:
        return False, f"Requires Manual Review: Damage estimate (₹{total_cost:,.2f}) exceeds ₹5,000 threshold and contains Moderate/Severe damage."
    elif not cost_ok:
        return False, f"Requires Manual Review: Damage estimate (₹{total_cost:,.2f}) exceeds auto-approval threshold of ₹5,000."
    else:
        return False, "Requires Manual Review: Non-minor (Moderate or Severe) damage detected requiring adjuster verification."


def create_claim_record(
    db_connection,
    user_id: int,
    user_email: str,
    report_id: int,
    policy_number: str,
    policy_type: str,
    total_cost: float,
    incident_description: str,
    incident_date: str,
    incident_location: str,
    severities: List[str]
) -> Dict[str, Any]:
    """
    Create an insurance claim in MySQL, apply auto-approval rules, and initialize the timeline.
    """
    coverage = calculate_claim_coverage(total_cost, policy_type)
    is_auto, reason = check_auto_approval(total_cost, severities)

    claim_number = generate_claim_number()
    initial_status = 'Approved' if is_auto else 'Submitted'

    with db_connection.cursor(dictionary=True) as cursor:
        cursor.execute('''
            INSERT INTO insurance_claims (
                claim_number, user_id, user_email, report_id, policy_number, policy_type,
                total_damage_cost, coverage_percentage, covered_amount, out_of_pocket,
                status, is_auto_approved, incident_description, incident_date,
                incident_location, admin_notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            claim_number, user_id, user_email, report_id, policy_number, coverage['policy_type'],
            coverage['total_damage_cost'], coverage['coverage_percentage'],
            coverage['covered_amount'], coverage['out_of_pocket'],
            initial_status, is_auto, incident_description, incident_date,
            incident_location, reason if is_auto else "Awaiting adjuster initial triage"
        ))
        claim_id = cursor.lastrowid

        # Timeline event 1: Submission
        cursor.execute('''
            INSERT INTO claim_timeline (claim_id, status, title, description, created_by)
            VALUES (%s, %s, %s, %s, %s)
        ''', (
            claim_id, 'Submitted', 'Claim Filed & Submitted',
            f"Claim {claim_number} officially filed by {user_email}. Policy: {policy_number} ({coverage['policy_type']}).",
            'Policyholder'
        ))

        # Timeline event 2: Auto-approval or Under Review
        if is_auto:
            cursor.execute('''
                INSERT INTO claim_timeline (claim_id, status, title, description, created_by)
                VALUES (%s, %s, %s, %s, %s)
            ''', (
                claim_id, 'Approved', 'Fast-Track AI Auto-Approval Granted',
                f"{reason} Payout authorized for ₹{coverage['covered_amount']:,.2f}.",
                'System AI Engine'
            ))
        else:
            cursor.execute('''
                INSERT INTO claim_timeline (claim_id, status, title, description, created_by)
                VALUES (%s, %s, %s, %s, %s)
            ''', (
                claim_id, 'Under Review', 'Queued for Adjuster Review',
                f"{reason}. An insurance claims specialist has been assigned.",
                'System AI Engine'
            ))

        db_connection.commit()

    return {
        'claim_id': claim_id,
        'claim_number': claim_number,
        'status': initial_status,
        'is_auto_approved': is_auto,
        'covered_amount': coverage['covered_amount'],
        'out_of_pocket': coverage['out_of_pocket'],
        'reason': reason
    }


def generate_claim_pdf(
    claim: Dict[str, Any],
    detections: List[Dict[str, Any]],
    user_info: Dict[str, Any]
) -> bytes:
    """
    Generate a professional Insurance Claim Assessment PDF Report using ReportLab.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    story = []
    styles = getSampleStyleSheet()

    # Custom typography styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#1E3A8A')
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#6B7280')
    )
    section_title = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#1E293B'),
        spaceBefore=14,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#374151')
    )
    bold_style = ParagraphStyle(
        'DocBold',
        parent=body_style,
        fontName='Helvetica-Bold'
    )

    # 1. Header Banner Table
    header_data = [
        [
            Paragraph("<b>DAMAGEVISION INSURANCE SERVICES</b><br/><font size=8 color='#6B7280'>Automated AI Damage Assessment & Claim Settlement</font>", body_style),
            Paragraph(f"<b>CLAIM REFERENCE:</b><br/><font size=12 color='#1E3A8A'><b>{claim.get('claim_number', 'N/A')}</b></font>", body_style)
        ]
    ]
    header_table = Table(header_data, colWidths=[340, 200])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#2563EB'), spaceAfter=14))

    # 2. Status & Overview Bar
    status_text = claim.get('status', 'Submitted').upper()
    status_bg = colors.HexColor(CLAIM_STATUS_COLORS.get(claim.get('status', 'Submitted'), '#3B82F6'))
    
    meta_data = [
        [
            Paragraph(f"<b>Date Filed:</b> {claim.get('created_at', datetime.now()).strftime('%Y-%m-%d %H:%M') if hasattr(claim.get('created_at'), 'strftime') else 'Recent'}", body_style),
            Paragraph(f"<b>Policy Number:</b> {claim.get('policy_number', 'N/A')}", body_style),
            Paragraph(f"<b>Status:</b> <b>{status_text}</b>", bold_style)
        ]
    ]
    meta_table = Table(meta_data, colWidths=[180, 180, 180])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F3F4F6')),
        ('PADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 12))

    # 3. Policyholder & Vehicle Info Grid
    story.append(Paragraph("1. Policyholder & Vehicle Specifications", section_title))
    info_data = [
        [
            Paragraph(f"<b>Policyholder:</b> {user_info.get('name', 'N/A')}", body_style),
            Paragraph(f"<b>Vehicle ID / VIN:</b> {user_info.get('vehicle_id', 'N/A')}", body_style)
        ],
        [
            Paragraph(f"<b>Registered Email:</b> {claim.get('user_email', 'N/A')}", body_style),
            Paragraph(f"<b>Make & Model:</b> {claim.get('car_brand', 'Generic')} {claim.get('car_model', '')}", body_style)
        ],
        [
            Paragraph(f"<b>Contact Phone:</b> {user_info.get('contact_number', 'N/A')}", body_style),
            Paragraph(f"<b>Policy Coverage Plan:</b> {claim.get('policy_type', 'Standard')} ({claim.get('coverage_percentage', 80)}% Coverage)", body_style)
        ],
        [
            Paragraph(f"<b>Incident Location:</b> {claim.get('incident_location') or 'Not Specified'}", body_style),
            Paragraph(f"<b>Incident Date:</b> {claim.get('incident_date') or 'Recent'}", body_style)
        ]
    ]
    info_table = Table(info_data, colWidths=[270, 270])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FAFAFA')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB')),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    # 4. YOLO AI Damage Localization & Severity Breakdown
    story.append(Paragraph("2. Computer Vision Damage Assessment & Severity Rating", section_title))
    det_headers = [
        Paragraph("<b>#</b>", bold_style),
        Paragraph("<b>Part Name</b>", bold_style),
        Paragraph("<b>Severity Rating</b>", bold_style),
        Paragraph("<b>AI Confidence</b>", bold_style),
        Paragraph("<b>Base Price</b>", bold_style),
        Paragraph("<b>Multiplier</b>", bold_style),
        Paragraph("<b>Final Price (₹)</b>", bold_style)
    ]
    det_table_data = [det_headers]

    for idx, d in enumerate(detections):
        sev_color = '#10B981' if d.get('severity') == 'Minor' else ('#F59E0B' if d.get('severity') == 'Moderate' else '#EF4444')
        det_table_data.append([
            Paragraph(str(idx + 1), body_style),
            Paragraph(f"<b>{d.get('part_name', 'Part')}</b>", body_style),
            Paragraph(f"<font color='{sev_color}'><b>{d.get('severity', 'Minor')}</b></font>", body_style),
            Paragraph(f"{round(float(d.get('confidence', 0.85))*100, 1)}%", body_style),
            Paragraph(f"₹{float(d.get('base_price', 200)):,.2f}", body_style),
            Paragraph(f"{d.get('multiplier', 1.0)}×", body_style),
            Paragraph(f"<b>₹{float(d.get('final_price', 200)):,.2f}</b>", body_style)
        ])

    if len(detections) == 0:
        det_table_data.append([Paragraph("No detected damages recorded.", body_style)] * 7)

    det_table = Table(det_table_data, colWidths=[30, 110, 85, 75, 75, 65, 100])
    det_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(det_table)
    story.append(Spacer(1, 14))

    # 5. Financial Settlement & Coverage Summary
    story.append(Paragraph("3. Financial Settlement & Compensation Summary", section_title))
    tot_cost = float(claim.get('total_damage_cost', 0.0))
    cov_pct = float(claim.get('coverage_percentage', 80.0))
    cov_amt = float(claim.get('covered_amount', 0.0))
    ded_amt = float(claim.get('out_of_pocket', 0.0))

    settle_data = [
        [
            Paragraph("Total Verified Repair Estimate:", body_style),
            Paragraph(f"<b>₹{tot_cost:,.2f}</b>", bold_style)
        ],
        [
            Paragraph(f"Insurance Policy Coverage ({cov_pct:.0f}% of total):", body_style),
            Paragraph(f"<font color='#059669'><b>- ₹{cov_amt:,.2f} (Payable by Insurer)</b></font>", bold_style)
        ],
        [
            Paragraph("Policyholder Out-of-Pocket Deductible:", body_style),
            Paragraph(f"<font color='#DC2626'><b>₹{ded_amt:,.2f} (Payable by User)</b></font>", bold_style)
        ]
    ]
    settle_table = Table(settle_data, colWidths=[360, 180])
    settle_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F9FAFB')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB')),
        ('PADDING', (0,0), (-1,-1), 7),
    ]))
    story.append(settle_table)
    story.append(Spacer(1, 16))

    # 6. Verification / Auto-approval Notice
    notice_title = "FAST-TRACK AI AUTO-APPROVAL NOTICE" if claim.get('is_auto_approved') else "CLAIM REVIEW DISCLOSURE"
    notice_text = (
        f"<b>{notice_title}:</b> This claim was evaluated by the DamageVision Automated Triage engine. "
        + (claim.get('admin_notes') or "Claim meets automated underwriting guidelines.")
    )
    notice_p = Paragraph(notice_text, body_style)
    notice_table = Table([[notice_p]], colWidths=[540])
    notice_bg = colors.HexColor('#ECFDF5') if claim.get('is_auto_approved') else colors.HexColor('#EFF6FF')
    notice_border = colors.HexColor('#10B981') if claim.get('is_auto_approved') else colors.HexColor('#3B82F6')
    notice_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), notice_bg),
        ('BOX', (0,0), (-1,-1), 1, notice_border),
        ('PADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(notice_table)
    story.append(Spacer(1, 20))

    # Footer signature block
    sig_data = [
        [
            Paragraph("<b>Generated By:</b> DamageVision AI System<br/>Verification Token: DV-SEC-" + claim.get('claim_number', '000')[-6:], subtitle_style),
            Paragraph("<b>Authorized Claims Underwriter:</b><br/>DamageVision Insurance Automation Desk", subtitle_style)
        ]
    ]
    sig_table = Table(sig_data, colWidths=[270, 270])
    story.append(sig_table)

    doc.build(story)
    return buffer.getvalue()
