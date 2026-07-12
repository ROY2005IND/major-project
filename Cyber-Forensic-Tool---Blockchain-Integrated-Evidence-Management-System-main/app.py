from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user
import os
from datetime import datetime

from config import Config
from models import db, User, Case
from auth import auth_bp
from forensic_engine.orchestrator import ForensicOrchestrator
from blockchain.blockchain_handler import BlockchainHandler
from llm_engine.report_generator import ReportGenerator

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config.from_object(Config)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///forensic_tool.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login_page'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    # API calls (fetch/JSON) should get a 401 they can handle in JS,
    # not a redirect to an HTML login page.
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'error': 'Not authenticated'}), 401
    return redirect(url_for('auth.login_page'))


app.register_blueprint(auth_bp)

blockchain_handler = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_active_case():
    """
    Return the Case row for the investigation currently 'open' in this
    browser session, or None.

    Any authenticated investigator may open any case - that's intentional.
    Chain-of-custody only means something if evidence can be handed between
    investigators and everyone can see the full history; restricting a case
    to only its creator would defeat the purpose.
    """
    case_id = session.get('active_case_id')
    if not case_id:
        return None
    return Case.query.get(case_id)


def case_or_error():
    case = get_active_case()
    if not case:
        return None, (jsonify({
            'status': 'error',
            'error': 'No active case. Start a forensic investigation first, or resume one from your dashboard.'
        }), 400)
    return case, None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route('/', endpoint='dashboard')
def index():
    """Landing page for anonymous visitors; dashboard for logged-in investigators."""
    if current_user.is_authenticated:
        return render_template('dashboard.html',
                                investigator=current_user.full_name,
                                organization=current_user.organization or Config.ORGANIZATION,
                                is_admin=current_user.is_admin())
    return render_template('landing.html')


@app.route('/workspace')
@login_required
def workspace():
    """The forensic investigation workspace (collect, register, report)."""
    return render_template('index.html',
                            investigator=current_user.full_name,
                            organization=current_user.organization or Config.ORGANIZATION)


# ---------------------------------------------------------------------------
# Case management
# ---------------------------------------------------------------------------

@app.route('/api/cases', methods=['GET'])
@login_required
def list_cases():
    try:
        scope = request.args.get('scope', 'mine')  # 'mine' | 'all'

        if scope == 'all':
            cases = Case.query.order_by(Case.created_at.desc()).all()
        else:
            cases = Case.query.filter_by(investigator_id=current_user.id) \
                               .order_by(Case.created_at.desc()).all()

        return jsonify({
            'status': 'success',
            'cases': [c.to_dict() for c in cases],
            'active_case_id': session.get('active_case_id')
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/cases/<int:case_id>/activate', methods=['POST'])
@login_required
def activate_case(case_id):
    try:
        case = Case.query.get(case_id)
        if not case:
            return jsonify({'status': 'error', 'error': 'Case not found'}), 404

        session['active_case_id'] = case.id
        return jsonify({'status': 'success', 'case': case.to_dict()})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/cases/active', methods=['GET'])
@login_required
def active_case_status():
    case = get_active_case()
    if not case:
        return jsonify({'status': 'no_session', 'message': 'No active case'})

    return jsonify({
        'status': 'active',
        'case': case.to_dict()
    })


# ---------------------------------------------------------------------------
# Misc info endpoints (unchanged behaviour, now behind login)
# ---------------------------------------------------------------------------

@app.route('/api/check-tools', methods=['GET'])
@login_required
def check_tools():
    try:
        available_tools = ForensicOrchestrator.list_available_tools()
        return jsonify({
            'status': 'success',
            'tools': available_tools,
            'system_info': Config.get_system_info()
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/system-info', methods=['GET'])
@login_required
def system_info():
    try:
        return jsonify({
            'status': 'success',
            'system': Config.get_system_info(),
            'core_tools': Config.CORE_TOOLS,
            'optional_tools': Config.OPTIONAL_TOOLS
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/validate-config', methods=['GET'])
@login_required
def validate_config():
    try:
        errors = Config.validate_config()
        if errors:
            return jsonify({'status': 'warning', 'errors': errors,
                             'message': 'Some configuration values are missing'})
        return jsonify({'status': 'success', 'message': 'Configuration is valid'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Core forensic workflow
# ---------------------------------------------------------------------------

@app.route('/api/start-forensics', methods=['POST'])
@login_required
def start_forensics():
    try:
        data = request.json or {}
        forensic_types = data.get('forensic_types', ['all'])
        use_advanced_tools = data.get('use_advanced_tools', False)

        session_dir, session_id = Config.create_session_directory()

        print(f"\n{'='*60}")
        print(f"FORENSIC INVESTIGATION STARTED")
        print(f"Investigator: {current_user.username}")
        print(f"Session ID: {session_id}")
        print(f"Session Directory: {session_dir}")
        print(f"Forensic Types: {', '.join(forensic_types)}")
        print(f"Advanced Tools: {'ENABLED' if use_advanced_tools else 'DISABLED'}")
        print(f"{'='*60}\n")

        orchestrator = ForensicOrchestrator(session_dir, session_id, use_advanced_tools)
        evidence_data = orchestrator.execute_forensics(forensic_types)
        orchestrator.save_master_json()
        evidence_hash = orchestrator.calculate_evidence_hash()

        print(f"\n[+] Evidence hash calculated: {evidence_hash}")

        actual_types = [t for t in evidence_data['forensics'].keys()]

        case = Case(
            session_id=session_id,
            session_dir=session_dir,
            investigator_id=current_user.id,
            evidence_hash=evidence_hash,
            forensic_types=','.join(actual_types),
            status='collected'
        )
        db.session.add(case)
        db.session.commit()

        session['active_case_id'] = case.id

        return jsonify({
            'status': 'success',
            'case_id': case.id,
            'session_id': session_id,
            'session_dir': session_dir,
            'evidence_hash': evidence_hash,
            'forensics_completed': actual_types,
            'advanced_tools_used': use_advanced_tools,
            'tools_summary': evidence_data.get('tools_summary', {}),
        })

    except Exception as e:
        db.session.rollback()
        print(f"\n[!] Error in forensic execution: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/register-blockchain', methods=['POST'])
@login_required
def register_blockchain():
    global blockchain_handler

    case, err = case_or_error()
    if err:
        return err

    try:
        print(f"\n{'='*60}")
        print(f"BLOCKCHAIN REGISTRATION")
        print(f"{'='*60}\n")

        if not blockchain_handler:
            blockchain_handler = BlockchainHandler()

        orchestrator = ForensicOrchestrator(case.session_dir, case.session_id)
        orchestrator.load_from_master_json()
        summary = orchestrator.get_evidence_summary()

        evidence_id = BlockchainHandler.generate_evidence_id(case.session_id)

        blockchain_result = blockchain_handler.register_evidence(
            evidence_id=evidence_id,
            evidence_hash=summary['evidence_hash'],
            os_source=summary['os_source'],
            forensic_type=','.join(summary['forensic_types']),
            tools_used=','.join(summary['tools_used'][:5]),
            investigator_id=current_user.username
        )

        if blockchain_result['status'] == 'success':
            print(f"\n[+] Evidence successfully registered on blockchain!")
            print(f"    Evidence ID: {evidence_id}")
            print(f"    Transaction: {blockchain_result['transaction_hash']}")
            print(f"    Block: {blockchain_result['block_number']}")

            case.blockchain_registered = True
            case.evidence_id = evidence_id
            case.transaction_hash = blockchain_result['transaction_hash']
            case.block_number = blockchain_result['block_number']
            case.status = 'registered'
            db.session.commit()

            return jsonify({
                'status': 'success',
                'evidence_id': evidence_id,
                'blockchain_data': blockchain_result,
                'case': case.to_dict()
            })
        else:
            return jsonify({'status': 'error', 'error': blockchain_result.get('error', 'Unknown error')}), 500

    except Exception as e:
        print(f"\n[!] Blockchain registration error: {str(e)}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/generate-report', methods=['POST'])
@login_required
def generate_report():
    case, err = case_or_error()
    if err:
        return err

    if not case.blockchain_registered:
        return jsonify({'status': 'error', 'error': 'Blockchain registration required before report generation.'}), 400

    try:
        print(f"\n{'='*60}")
        print(f"REPORT GENERATION")
        print(f"{'='*60}\n")

        orchestrator = ForensicOrchestrator(case.session_dir, case.session_id)
        evidence_data = orchestrator.load_from_master_json()

        blockchain_data = {
            'status': 'success',
            'transaction_hash': case.transaction_hash,
            'block_number': case.block_number,
            'evidence_id': case.evidence_id
        }

        report_gen = ReportGenerator()
        report = report_gen.generate_comprehensive_report(
            evidence_data=evidence_data,
            blockchain_data=blockchain_data
        )

        report_paths = report_gen.save_report(report, case.session_dir)

        print(f"\n[+] Report generation completed!")
        print(f"    JSON Report: {report_paths['json_report']}")
        print(f"    Text Report: {report_paths['text_report']}")

        case.report_generated = True
        case.report_json_path = report_paths['json_report']
        case.report_text_path = report_paths['text_report']
        case.status = 'reported'
        db.session.commit()

        return jsonify({
            'status': 'success',
            'report': report,
            'report_paths': report_paths,
            'case': case.to_dict()
        })

    except Exception as e:
        print(f"\n[!] Report generation error: {str(e)}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/check-integrity', methods=['GET'])
@login_required
def check_integrity():
    """
    Tamper-detection check: recompute the SHA-256 hash of the evidence currently
    on disk and compare it against (a) the hash captured at collection time and
    (b) the hash immutably recorded on the blockchain (if registered).
    """
    global blockchain_handler

    case, err = case_or_error()
    if err:
        return err

    try:
        orchestrator = ForensicOrchestrator(case.session_dir, case.session_id)
        orchestrator.load_from_master_json()
        current_hash = orchestrator.compute_hash_readonly()

        original_hash = case.evidence_hash
        local_match = (current_hash == original_hash)

        result = {
            'status': 'success',
            'checked_at': datetime.now().isoformat(),
            'original_hash': original_hash,
            'current_hash': current_hash,
            'local_integrity_ok': local_match,
            'blockchain_checked': False,
            'blockchain_integrity_ok': None,
            'overall_status': 'INTACT' if local_match else 'TAMPERED'
        }

        if case.blockchain_registered and case.evidence_id:
            try:
                if not blockchain_handler:
                    blockchain_handler = BlockchainHandler()

                is_valid_onchain = blockchain_handler.verify_evidence_hash(
                    case.evidence_id,
                    current_hash
                )
                result['blockchain_checked'] = True
                result['blockchain_integrity_ok'] = is_valid_onchain
                result['evidence_id'] = case.evidence_id

                if not is_valid_onchain:
                    result['overall_status'] = 'TAMPERED'
            except Exception as chain_err:
                result['blockchain_check_error'] = str(chain_err)

        return jsonify(result)

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Chain of custody
# ---------------------------------------------------------------------------

@app.route('/api/custody-event', methods=['POST'])
@login_required
def add_custody_event():
    """
    Log a chain-of-custody event (Accessed / Analyzed / Transferred / etc.)
    for the active case's on-chain evidence record. Requires the case to
    already be registered on the blockchain, since custody is tracked
    against the on-chain evidence ID.
    """
    global blockchain_handler

    case, err = case_or_error()
    if err:
        return err

    if not case.blockchain_registered or not case.evidence_id:
        return jsonify({'status': 'error', 'error': 'Register this case on the blockchain first.'}), 400

    try:
        data = request.json or {}
        action = (data.get('action') or '').strip()
        remarks = (data.get('remarks') or '').strip()

        if not action:
            return jsonify({'status': 'error', 'error': 'Action is required (e.g. Accessed, Analyzed, Transferred)'}), 400

        if not blockchain_handler:
            blockchain_handler = BlockchainHandler()

        result = blockchain_handler.add_custody_event(
            evidence_id=case.evidence_id,
            action=action,
            remarks=remarks or f'{action} by {current_user.full_name}',
            investigator_id=current_user.username
        )

        if result.get('status') != 'success':
            return jsonify({'status': 'error', 'error': result.get('error', 'Unknown error')}), 500

        return jsonify({'status': 'success', 'result': result})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/custody-chain', methods=['GET'])
@login_required
def custody_chain():
    global blockchain_handler

    case, err = case_or_error()
    if err:
        return err

    if not case.blockchain_registered or not case.evidence_id:
        return jsonify({'status': 'success', 'custody_chain': []})

    try:
        if not blockchain_handler:
            blockchain_handler = BlockchainHandler()

        chain = blockchain_handler.get_custody_chain(case.evidence_id)
        if isinstance(chain, dict) and chain.get('status') == 'error':
            return jsonify({'status': 'error', 'error': chain.get('error')}), 500

        return jsonify({'status': 'success', 'custody_chain': chain})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Reports / blockchain misc
# ---------------------------------------------------------------------------

@app.route('/api/download-report/<report_type>', methods=['GET'])
@login_required
def download_report(report_type):
    case, err = case_or_error()
    if err:
        return err

    if not case.report_generated:
        return jsonify({'status': 'error', 'error': 'No report available'}), 404

    try:
        if report_type == 'json':
            filepath = case.report_json_path
            mimetype = 'application/json'
        elif report_type == 'text':
            filepath = case.report_text_path
            mimetype = 'text/plain'
        else:
            return jsonify({'status': 'error', 'error': 'Invalid report type'}), 400

        return send_file(filepath, mimetype=mimetype, as_attachment=True,
                          download_name=os.path.basename(filepath))

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/blockchain-balance', methods=['GET'])
@login_required
def blockchain_balance():
    global blockchain_handler
    try:
        if not blockchain_handler:
            blockchain_handler = BlockchainHandler()
        balance = blockchain_handler.get_account_balance()
        return jsonify({'status': 'success', 'balance': balance})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/verify-evidence', methods=['POST'])
@login_required
def verify_evidence():
    global blockchain_handler
    try:
        data = request.json or {}
        evidence_id = data.get('evidence_id')
        hash_to_verify = data.get('hash')

        if not blockchain_handler:
            blockchain_handler = BlockchainHandler()

        is_valid = blockchain_handler.verify_evidence_hash(evidence_id, hash_to_verify)

        return jsonify({
            'status': 'success',
            'is_valid': is_valid,
            'message': 'Hash verified successfully' if is_valid else 'Hash mismatch'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({'status': 'error', 'error': 'Endpoint not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'status': 'error', 'error': 'Internal server error'}), 500


if __name__ == '__main__':
    os.makedirs(Config.BASE_EVIDENCE_DIR, exist_ok=True)

    with app.app_context():
        db.create_all()

    errors = Config.validate_config()
    if errors:
        print("\n" + "="*60)
        print("CONFIGURATION WARNINGS:")
        for error in errors:
            print(f"  ⚠️  {error}")
        print("="*60 + "\n")
        print("Some features may not work without proper configuration.")
        print("Please check your .env file.\n")

    print("\n" + "="*60)
    print("CYBER FORENSIC TOOL")
    print("="*60)
    print(f"Evidence Directory: {Config.BASE_EVIDENCE_DIR}")
    print(f"Database: forensic_tool.db")
    print("First registered account automatically becomes admin.")
    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=Config.DEBUG)
