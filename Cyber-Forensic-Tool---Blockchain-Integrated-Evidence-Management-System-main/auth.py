"""
Authentication routes: login, register, logout.

Kept as a separate blueprint so app.py's forensic/blockchain routes
don't get cluttered. Registration is open (any investigator can create
their own account) - the FIRST account ever created automatically
becomes an admin, every account after that is a regular investigator.
An admin can promote others later directly in the database if needed.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user

from models import db, User

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET'])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@auth_bp.route('/register', methods=['GET'])
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@auth_bp.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.json or {}
        username = (data.get('username') or '').strip().lower()
        full_name = (data.get('full_name') or '').strip()
        organization = (data.get('organization') or '').strip()
        password = data.get('password') or ''
        confirm_password = data.get('confirm_password') or ''

        if not username or not full_name or not password:
            return jsonify({'status': 'error', 'error': 'Username, full name, and password are required'}), 400

        if len(password) < 6:
            return jsonify({'status': 'error', 'error': 'Password must be at least 6 characters'}), 400

        if password != confirm_password:
            return jsonify({'status': 'error', 'error': 'Passwords do not match'}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({'status': 'error', 'error': 'Username already taken'}), 400

        # First-ever account becomes admin automatically
        is_first_user = User.query.count() == 0
        role = 'admin' if is_first_user else 'investigator'

        user = User(
            username=username,
            full_name=full_name,
            organization=organization,
            role=role
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)

        return jsonify({'status': 'success', 'user': user.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'error': str(e)}), 500


@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.json or {}
        username = (data.get('username') or '').strip().lower()
        password = data.get('password') or ''
        remember = bool(data.get('remember', False))

        user = User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            return jsonify({'status': 'error', 'error': 'Invalid username or password'}), 401

        login_user(user, remember=remember)

        return jsonify({'status': 'success', 'user': user.to_dict()})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@auth_bp.route('/api/auth/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'status': 'success', 'message': 'Logged out'})


@auth_bp.route('/api/auth/me', methods=['GET'])
@login_required
def me():
    return jsonify({'status': 'success', 'user': current_user.to_dict()})
