-- ==============================================================
-- Vehicle Damage Detection System - Database Migration
-- Adds Severity Classification & Admin Dashboard Support
-- ==============================================================

USE vehicle_damage_detection;

-- 1. Add is_admin column to user_info if not exists
SET @dbname = DATABASE();
SET @tablename = 'user_info';
SET @columnname = 'is_admin';
SET @preparedStatement = (SELECT IF(
  (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE
      (TABLE_NAME = @tablename)
      AND (TABLE_SCHEMA = @dbname)
      AND (COLUMN_NAME = @columnname)
  ) > 0,
  'SELECT 1',
  'ALTER TABLE user_info ADD COLUMN is_admin BOOLEAN DEFAULT FALSE'
));
PREPARE alterIfNotExists FROM @preparedStatement;
EXECUTE alterIfNotExists;
DEALLOCATE PREPARE alterIfNotExists;

-- 2. Create damage_reports table
CREATE TABLE IF NOT EXISTS damage_reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    user_email VARCHAR(255) NOT NULL,
    original_image VARCHAR(255) NOT NULL,
    detected_image VARCHAR(255) NOT NULL,
    car_brand VARCHAR(100),
    car_model VARCHAR(100),
    total_cost DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Create damage_detections table
CREATE TABLE IF NOT EXISTS damage_detections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    report_id INT NOT NULL,
    part_name VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL, -- 'Minor', 'Moderate', 'Severe'
    confidence DECIMAL(5, 4),
    bbox_area_ratio DECIMAL(6, 4),
    overlap_count INT DEFAULT 0,
    base_price DECIMAL(10, 2) NOT NULL,
    multiplier DECIMAL(4, 2) NOT NULL,
    final_price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES damage_reports(id) ON DELETE CASCADE
);

-- 4. Set admin user
UPDATE user_info SET is_admin = TRUE WHERE email = 'sonawaneaditya2345@gmail.com';
