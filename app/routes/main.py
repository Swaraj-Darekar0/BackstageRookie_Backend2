from flask import Blueprint, send_file, session, current_app
# ### NEW IMPORTS for Celery ###
from celery_app import celery
from app.tasks import run_analysis_task, generate_report_task
# ###############################
from app.services.github_service import GitHubService
from app.services.analysis_service import AnalysisService
from app.services.report_service import ReportService
import os
import json
import uuid
import shutil
import stat # stat is only used by _remove_readonly_onerror, which is being moved
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
import google.generativeai as genai
from google.auth.transport import requests as google_requests

from functools import wraps
from flask import request, jsonify


main_bp = Blueprint('main', __name__)


# Global variable to store current plan (in production, use database)
CURRENT_PLAN = 'basic'  # Default plan

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


# Removed _remove_readonly_onerror as it is now part of the Celery task


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
    user_token = session.get('google_access_token')

    if not user_token:
        return jsonify({"error": "Unauthorized: User token not found in session."}), 401

    try:
        print(f"[/api/analyze] Dispatching analysis task for URL: {github_url}")
        task = run_analysis_task.delay(github_url, sector_hint, framework_hint, plan, user_token)
        return jsonify({'status': 'accepted', 'task_id': task.id, 'message': 'Analysis started in background.'}), 202
    except Exception as e:
        print(f"[/api/analyze] ERROR dispatching analysis task: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Failed to start analysis: {str(e)}'}), 500


@main_bp.route('/api/generate-report', methods=['POST'])
@login_required
def generate_report():
    data = request.get_json()
    scan_id = data.get('scan_id')
    report_type = data.get('report_type')
    model_name = data.get('model_name', 'models/gemini-1.5-pro-latest') # Get model_name, with a default

    user_token = session.get('google_access_token')
    if not user_token:
        return jsonify({'status': 'error', 'message': 'User token not found in session.'}), 401
    
    try:
        print(f"[/api/generate-report] Dispatching report generation task for Scan ID: {scan_id}, Type: {report_type}, Model: {model_name}")
        task = generate_report_task.delay(scan_id, report_type, user_token, model_name)
        return jsonify({'status': 'accepted', 'task_id': task.id, 'message': 'Report generation started in background.'}), 202
    except Exception as e:
        print(f"[/api/generate-report] ERROR dispatching report generation task: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Failed to start report generation: {str(e)}'}), 500

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
        # Correctly create a Credentials object from the user's access token
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


@main_bp.route('/api/task_status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    task = celery.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': 'Task is pending or has not started.'
        }
    elif task.state == 'PROGRESS':
        response = {
            'state': task.state,
            'status': task.info.get('status', 'Task is running.'),
            'progress': task.info.get('progress', 0)
        }
    elif task.state == 'SUCCESS':
        response = {
            'state': task.state,
            'status': 'Task completed successfully.',
            'result': task.result # This will be the return value of the task
        }
    elif task.state == 'FAILURE':
        response = {
            'state': task.state,
            'status': 'Task failed.',
            'error': str(task.info) # task.info contains the exception and traceback
        }
    else:
        response = {
            'state': task.state,
            'status': 'Unknown task state.'
        }
        return jsonify(response)
    
    @main_bp.route('/api/download/<task_id>', methods=['GET'])
    @login_required
    def download_report(task_id):
        task = celery.AsyncResult(task_id)
        if task.state == 'SUCCESS':
            result = task.result
            if result and 'report_path' in result and os.path.exists(result['report_path']):
                return send_file(result['report_path'], as_attachment=True)
            else:
                return jsonify({'error': 'Report file not found or path is missing.'}), 404
        elif task.state == 'FAILURE':
            return jsonify({'error': 'Task failed and did not produce a report.', 'details': str(task.info)}), 500
        else:
            return jsonify({'error': 'Task is not yet complete. Please wait.'}), 202
    
