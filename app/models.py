"""
SQLAlchemy models mapped onto the tables in database/schema.sql. The schema
file is the source of truth: these classes describe the existing tables and
never create or alter them. Table names are uppercase because MySQL table
names are case-sensitive on Linux.
"""
from flask_login import UserMixin

from app.extensions import db

SEVERITY_LEVELS = ("normal", "moderate", "high")
ALERT_CHANNELS = ("dashboard", "email")
ALERT_STATUSES = ("pending", "sent", "acknowledged", "failed")
USER_ROLES = ("authority", "responder", "public", "administrator")


class Region(db.Model):
    __tablename__ = "REGION"

    region_id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    county = db.Column(db.String(100), nullable=False)
    latitude = db.Column(db.Numeric(9, 6), nullable=False)
    longitude = db.Column(db.Numeric(9, 6), nullable=False)

    readings = db.relationship("SensorReading", back_populates="region")


class SensorReading(db.Model):
    __tablename__ = "SENSOR_READING"
    __table_args__ = (db.UniqueConstraint("region_id", "timestamp", name="uq_region_timestamp"),)

    reading_id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey("REGION.region_id"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    rainfall = db.Column(db.Float)
    river_discharge = db.Column(db.Float)
    humidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    soil_moisture = db.Column(db.Float)

    region = db.relationship("Region", back_populates="readings")
    anomaly = db.relationship("Anomaly", back_populates="reading", uselist=False)


class Anomaly(db.Model):
    __tablename__ = "ANOMALY"

    anomaly_id = db.Column(db.Integer, primary_key=True)
    reading_id = db.Column(db.Integer, db.ForeignKey("SENSOR_READING.reading_id"), unique=True, nullable=False)
    anomaly_score = db.Column(db.Float, nullable=False)
    severity_level = db.Column(db.Enum(*SEVERITY_LEVELS, name="severity_level"), nullable=False)
    detected_at = db.Column(db.DateTime, nullable=False, server_default=db.func.current_timestamp())

    reading = db.relationship("SensorReading", back_populates="anomaly")
    alerts = db.relationship("Alert", back_populates="anomaly")


class Alert(db.Model):
    __tablename__ = "ALERT"

    alert_id = db.Column(db.Integer, primary_key=True)
    anomaly_id = db.Column(db.Integer, db.ForeignKey("ANOMALY.anomaly_id"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    channel = db.Column(db.Enum(*ALERT_CHANNELS, name="channel"), nullable=False, server_default="dashboard")
    status = db.Column(db.Enum(*ALERT_STATUSES, name="status"), nullable=False, server_default="pending")
    sent_at = db.Column(db.DateTime)

    anomaly = db.relationship("Anomaly", back_populates="alerts")
    feedback = db.relationship("Feedback", back_populates="alert")


class User(UserMixin, db.Model):
    __tablename__ = "USER"

    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    role = db.Column(db.Enum(*USER_ROLES, name="role"), nullable=False, server_default="public")
    password_hash = db.Column(db.String(255), nullable=False)

    feedback = db.relationship("Feedback", back_populates="user")

    def get_id(self) -> str:  # Flask-Login stores this in the session cookie
        return str(self.user_id)

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "name": self.name, "email": self.email,
                "phone": self.phone, "role": self.role}


class Feedback(db.Model):
    __tablename__ = "FEEDBACK"

    feedback_id = db.Column(db.Integer, primary_key=True)
    alert_id = db.Column(db.Integer, db.ForeignKey("ALERT.alert_id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("USER.user_id"), nullable=False)
    is_confirmed = db.Column(db.Boolean, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.current_timestamp())

    alert = db.relationship("Alert", back_populates="feedback")
    user = db.relationship("User", back_populates="feedback")
