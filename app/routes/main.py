import os
import json
import uuid
import shutil
import stat
from functools import wraps

from flask import Blueprint, send_file, session, current_app, request, jsonify
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google.auth.transport import requests as google_requests
import google.generativeai as genai

from app.services.github_service import GitHubService
from app.services.analysis_service import AnalysisService
from app.services.report_service import ReportService
from app.services.django_info_service import extract_django_endpoints
from app.services.flaskFastApi_info_service import extract_flask_fastapi_endpoints
main_bp = Blueprint('main', __name__)

# Global variable to store current plan (in production, use database)
CURRENT_PLAN = 'basic'  # Default plan


def _remove_readonly_onerror(func, path, _):
    """
    Error handler for `shutil.rmtree`.
    If the error is due to an access error (read-only file), it attempts to
    add write permission and then retries the operation.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        # 1. Check for Bearer token in Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            # For now, simply having the token is enough.
            # You might want to validate it here.
            if 'google_access_token' not in session:
                session['google_access_token'] = token

        # 2. Fallback to checking session
        if "google_access_token" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@main_bp.route('/api/change-plan', methods=['POST'])
def change_plan():
    """Change analysis plan configuration"""
    global CURRENT_PLAN
    try:
        data = request.get_json()
        new_plan = data.get('plan')

        if new_plan not in ['basic', 'full']:
            return jsonify({'status': 'error', 'message': 'Invalid plan'}), 400

        # Update global plan
        CURRENT_PLAN = new_plan
        print(f"[/api/change-plan] Plan changed to: {new_plan}")

        return jsonify({
            'status': 'success',
            'plan': new_plan,
            'message': f'Plan changed to {new_plan}'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    """Clears the server-side session."""
    session.clear()
    return jsonify({"status": "success", "message": "Session cleared"})


@main_bp.route('/api/get-plan', methods=['GET'])
@login_required
def get_plan():
    """Get current plan"""
    global CURRENT_PLAN
    return jsonify({
        'status': 'success',
        'plan': CURRENT_PLAN
    })


@main_bp.route('/api/analyze', methods=['POST'])
@login_required
def analyze_repository():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    github_url = data.get('github_url')
    if not github_url:
        return jsonify({"error": "github_url is required"}), 400

    sector_hint = data.get('sector_hint') or 'General Data Privacy'
    framework_hint = data.get('backend_framework', '').lower()
    plan = data.get('plan', CURRENT_PLAN)
    scan_id = str(uuid.uuid4())
    repo_name = github_url.split('/')[-1].replace('.git', '')
    repo_path = os.path.join(current_app.config['PULLED_CODE_DIR'], scan_id, repo_name)
    user_token = session.get('google_access_token')

    try:
        print(f"[/api/analyze] Received request for scan {scan_id}")
        print(f"[/api/analyze] URL: {github_url}, Sector: {sector_hint}, Framework: {framework_hint}, Plan: {plan}")
        
        print(f"[/api/analyze] Phase 1: Cloning repository into isolated path: {repo_path}")
        github_service = GitHubService()
        github_service.clone_repository(github_url, repo_path)
        
        print(f"[/api/analyze] Phase 2: Starting codebase analysis with plan: {plan}...")
        analysis_service = AnalysisService(plan=plan)
        scan_results = analysis_service.analyze_codebase(repo_path, sector_hint, scan_id)
        
        framework_analysis_results = None
        if framework_hint:
            print(f"[/api/analyze] Starting framework analysis for: {framework_hint}")
            try:
                # Assuming these functions are imported or defined elsewhere
                if framework_hint == 'django':
                    framework_analysis_results = extract_django_endpoints(
                        repo_path=repo_path, 
                        user_token=user_token, 
                        sector=sector_hint
                    )
                elif framework_hint in ['flask', 'fastapi']:
                    framework_analysis_results = extract_flask_fastapi_endpoints(
                        repo_path=repo_path, 
                        user_token=user_token, 
                        sector=sector_hint
                    )
                
                if framework_analysis_results:
                    filename = f"{scan_id}_EndpointAnalysis.json"
                    save_path = os.path.join(current_app.config['DATA_DIR'], "scanned_results", filename)
                    with open(save_path, 'w') as f:
                        json.dump(framework_analysis_results, f, indent=2)
                    print(f"[/api/analyze] Successfully saved framework analysis to {save_path}")

            except Exception as fw_e:
                print(f"[/api/analyze] Framework analysis failed: {str(fw_e)}")
                framework_analysis_results = {"error": str(fw_e)}

        return jsonify({
            'status': 'success',
            'scan_id': scan_results['scan_id'],
            'plan_used': plan,
            'total_findings': scan_results['summary']['total_findings'],
            'framework_analysis': framework_analysis_results, 
            'message': f'Analysis completed successfully using {plan} plan'
        })
    except Exception as e:
        print(f"[/api/analyze] ERROR for scan {scan_id}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        parent_dir = os.path.dirname(repo_path)
        if os.path.exists(parent_dir):
            print(f"[/api/analyze] Cleaning up directory: {parent_dir}")
            shutil.rmtree(parent_dir, onerror=_remove_readonly_onerror)


@main_bp.route('/api/generate-report', methods=['POST'])
@login_required
def generate_report():
    try:
        data = request.get_json()
        scan_id = data.get('scan_id')
        report_type = data.get('report_type')
        model_name = data.get('model_name', 'models/gemini-2.5-pro-latest')

        user_token = session.get('google_access_token')
        if not user_token:
            return jsonify({'status': 'error', 'message': 'User token not found in session.'}), 401
        
        print(f"[/api/generate-report] Scan ID: {scan_id}, Type: {report_type}, Model: {model_name}")
        
        report_service = ReportService()
        report_path = report_service.generate_report(scan_id, report_type, user_token, model_name)
        
        return send_file(report_path, as_attachment=True)
    except Exception as e:
        print(f"[/api/generate-report] ERROR: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main_bp.route('/api/auth/me', methods=['GET'])
@login_required
def get_user_profile():
    """Get user profile from ID token"""
    if 'google_id_token' not in session:
        return jsonify({"error": "ID token not found in session"}), 401
    
    id_token_str = session['google_id_token']
    try:
        id_info = id_token.verify_oauth2_token(id_token_str, google_requests.Request())
        user_profile = {
            'name': id_info.get('name'),
            'email': id_info.get('email'),
            'picture': id_info.get('picture')
        }
        return jsonify(user_profile)
    except ValueError as e:
        return jsonify({"error": "Invalid ID token", "message": str(e)}), 401


@main_bp.route('/healthz')
def health_check():
    """Health check endpoint for Render."""
    return jsonify({"status": "healthy"}), 200


@main_bp.route('/api/models', methods=['GET'])
@login_required
def list_models():
    """List available Gemini models for the authenticated user."""
    user_token = session.get('google_access_token')
    if not user_token:
        return jsonify({'status': 'error', 'message': 'User token not found in session.'}), 401

    api_key_env = os.environ.pop('GOOGLE_API_KEY', None)
    gemini_key_env = os.environ.pop('GEMINI_API_KEY', None)

    try:
        user_credentials = Credentials(token=user_token)
        genai.configure(credentials=user_credentials, api_key=None)
        
        models_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                models_list.append({
                    'name': m.name,
                    'display_name': m.display_name,
                    'description': m.description,
                })
        return jsonify(models_list)
    except Exception as e:
        print(f"[/api/models] ERROR: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to list models.'}), 500
    finally:
        if api_key_env:
            os.environ['GOOGLE_API_KEY'] = api_key_env
        if gemini_key_env:
            os.environ['GEMINI_API_KEY'] = gemini_key_env
