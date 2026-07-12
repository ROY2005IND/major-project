"""
Database models for multi-investigator support.

Uses SQLite (via Flask-SQLAlchemy) so no external database server is
required - the whole thing is a single file (forensic_tool.db) created
automatically on first run. This is intentional: it keeps setup simple
for a college project while still being a "real" persistent store
instead of in-memory globals that reset on every server restart.
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model, UserMixin):
    """An investigator (or admin) account."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    organization = db.Column(db.String(120), default='')
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='investigator')  # 'investigator' | 'admin'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    cases = db.relationship('Case', backref='investigator', lazy=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def is_admin(self):
        return self.role == 'admin'

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'full_name': self.full_name,
            'organization': self.organization,
            'role': self.role
        }


class Case(db.Model):
    """
    One forensic investigation session, tied to the investigator who
    ran it. The bulky evidence data itself stays on disk (in the
    session's master_evidence.json / report files, exactly as before) -
    this row just tracks metadata + status so it can be listed,
    resumed, and attributed to the right investigator.
    """
    __tablename__ = 'cases'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    session_dir = db.Column(db.String(255), nullable=False)
    investigator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    evidence_hash = db.Column(db.String(64))
    forensic_types = db.Column(db.String(120))  # comma-separated

    blockchain_registered = db.Column(db.Boolean, default=False)
    evidence_id = db.Column(db.String(64))          # on-chain evidence id
    transaction_hash = db.Column(db.String(120))
    block_number = db.Column(db.Integer)

    report_generated = db.Column(db.Boolean, default=False)
    report_json_path = db.Column(db.String(255))
    report_text_path = db.Column(db.String(255))

    status = db.Column(db.String(20), default='collected')
    # collected -> registered -> reported

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'session_id': self.session_id,
            'session_dir': self.session_dir,
            'investigator': self.investigator.full_name if self.investigator else None,
            'investigator_username': self.investigator.username if self.investigator else None,
            'evidence_hash': self.evidence_hash,
            'forensic_types': self.forensic_types,
            'blockchain_registered': self.blockchain_registered,
            'evidence_id': self.evidence_id,
            'transaction_hash': self.transaction_hash,
            'block_number': self.block_number,
            'report_generated': self.report_generated,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
