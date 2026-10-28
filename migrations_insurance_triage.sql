-- ==============================================================
-- Vehicle Damage Detection - Insurance Claim Assistant & Damage Triage
-- Database Migrations
-- ==============================================================

USE vehicle_damage_detection;

-- 1. Add insurance policy and user classification fields to user_info
ALTER TABLE user_info 
ADD COLUMN IF NOT EXISTS insurance_provider VARCHAR(100) DEFAULT 'Royal Sundaram General Insurance',
ADD COLUMN IF NOT EXISTS policy_number VARCHAR(100) DEFAULT 'POL-2026-889901',
ADD COLUMN IF NOT EXISTS policy_type VARCHAR(50) DEFAULT 'Standard',
ADD COLUMN IF NOT EXISTS user_type VARCHAR(50) DEFAULT 'Personal';

-- 2. Add triage fields to damage_reports
ALTER TABLE damage_reports
ADD COLUMN IF NOT EXISTS triage_score INT DEFAULT 45,
ADD COLUMN IF NOT EXISTS priority VARCHAR(20) DEFAULT 'Medium',
ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(100) DEFAULT 'Senior Master Tech - John Doe',
ADD COLUMN IF NOT EXISTS technician_role VARCHAR(100) DEFAULT 'Bodywork & Structural Lead',
ADD COLUMN IF NOT EXISTS triage_status VARCHAR(50) DEFAULT 'Queued',
ADD COLUMN IF NOT EXISTS sla_hours INT DEFAULT 24,
ADD COLUMN IF NOT EXISTS sla_deadline TIMESTAMP NULL,
ADD COLUMN IF NOT EXISTS sla_breached BOOLEAN DEFAULT FALSE;

-- 3. Create insurance_claims table
CREATE TABLE IF NOT EXISTS insurance_claims (
    id INT AUTO_INCREMENT PRIMARY KEY,
    claim_number VARCHAR(50) NOT NULL UNIQUE,
    user_id INT,
    user_email VARCHAR(255) NOT NULL,
    report_id INT NOT NULL,
    policy_number VARCHAR(100) NOT NULL,
    policy_type VARCHAR(50) NOT NULL DEFAULT 'Standard',
    total_damage_cost DECIMAL(10, 2) NOT NULL,
    coverage_percentage DECIMAL(5, 2) NOT NULL DEFAULT 80.00,
    covered_amount DECIMAL(10, 2) NOT NULL,
    out_of_pocket DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'Submitted', -- 'Draft', 'Submitted', 'Under Review', 'Approved', 'Rejected'
    is_auto_approved BOOLEAN DEFAULT FALSE,
    incident_description TEXT,
    incident_date DATE,
    incident_location VARCHAR(255),
    admin_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES damage_reports(id) ON DELETE CASCADE
);

-- 4. Create claim_timeline table
CREATE TABLE IF NOT EXISTS claim_timeline (
    id INT AUTO_INCREMENT PRIMARY KEY,
    claim_id INT NOT NULL,
    status VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    created_by VARCHAR(100) DEFAULT 'System AI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (claim_id) REFERENCES insurance_claims(id) ON DELETE CASCADE
);

-- 5. Create triage_queue table
CREATE TABLE IF NOT EXISTS triage_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    report_id INT NOT NULL UNIQUE,
    triage_score INT NOT NULL,
    priority VARCHAR(20) NOT NULL, -- 'Critical', 'High', 'Medium', 'Low'
    assigned_to VARCHAR(100) NOT NULL,
    technician_role VARCHAR(100),
    sla_hours INT DEFAULT 24,
    sla_deadline TIMESTAMP NULL,
    sla_breached BOOLEAN DEFAULT FALSE,
    status VARCHAR(50) DEFAULT 'Queued',
    severity_breakdown VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES damage_reports(id) ON DELETE CASCADE
);
