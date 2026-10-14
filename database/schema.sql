USE flood_anomaly_detection;

CREATE TABLE IF NOT EXISTS REGION (
    region_id INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    county VARCHAR(100) NOT NULL,
    latitude DECIMAL(9,6) NOT NULL,
    longitude DECIMAL(9,6) NOT NULL,
    UNIQUE KEY uq_region_slug (slug)
);

CREATE TABLE IF NOT EXISTS SENSOR_READING (
    reading_id INT AUTO_INCREMENT PRIMARY KEY,
    region_id INT NOT NULL,
    `timestamp` DATETIME NOT NULL,
    rainfall FLOAT,
    river_discharge FLOAT,
    humidity FLOAT,
    temperature FLOAT,
    soil_moisture FLOAT,
    CONSTRAINT fk_reading_region FOREIGN KEY (region_id) REFERENCES REGION(region_id),
    UNIQUE KEY uq_region_timestamp (region_id, `timestamp`)
);

CREATE TABLE IF NOT EXISTS ANOMALY (
    anomaly_id INT AUTO_INCREMENT PRIMARY KEY,
    reading_id INT NOT NULL,
    anomaly_score FLOAT NOT NULL,
    severity_level ENUM('normal', 'moderate', 'high') NOT NULL,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_anomaly_reading FOREIGN KEY (reading_id) REFERENCES SENSOR_READING(reading_id),
    UNIQUE KEY uq_anomaly_reading (reading_id),
    INDEX idx_severity (severity_level)
);

CREATE TABLE IF NOT EXISTS ALERT (
    alert_id INT AUTO_INCREMENT PRIMARY KEY,
    anomaly_id INT NOT NULL,
    message VARCHAR(255) NOT NULL,
    channel ENUM('dashboard', 'email') NOT NULL DEFAULT 'dashboard',
    status ENUM('pending', 'sent', 'acknowledged', 'failed') NOT NULL DEFAULT 'pending',
    sent_at DATETIME NULL,
    CONSTRAINT fk_alert_anomaly FOREIGN KEY (anomaly_id) REFERENCES ANOMALY(anomaly_id)
);

CREATE TABLE IF NOT EXISTS `USER` (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    email VARCHAR(150) NOT NULL,
    phone VARCHAR(20),
    role ENUM('authority', 'responder', 'public', 'administrator') NOT NULL DEFAULT 'public',
    password_hash VARCHAR(255) NOT NULL,
    UNIQUE KEY uq_user_email (email)
);

CREATE TABLE IF NOT EXISTS FEEDBACK (
    feedback_id INT AUTO_INCREMENT PRIMARY KEY,
    alert_id INT NOT NULL,
    user_id INT NOT NULL,
    is_confirmed BOOLEAN NOT NULL,
    comment TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feedback_alert FOREIGN KEY (alert_id) REFERENCES ALERT(alert_id),
    CONSTRAINT fk_feedback_user FOREIGN KEY (user_id) REFERENCES `USER`(user_id)
);
