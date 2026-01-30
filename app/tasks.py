import os
import shutil
import stat
import json
import uuid
from datetime import datetime
from celery_app import celery
from flask import Flask, current_app
from app.services.github_service import GitHubService
from app.services.analysis_service import AnalysisService
from app.services.report_service import ReportService
from app.services.django_info_service import extract_django_endpoints
from app.services.flaskFastApi_info_service import extract_flask_fastapi_endpoints
from google.oauth2.credentials import Credentials
import google.generativeai as genai


# Helper function for cleanup
def _remove_readonly_onerror(func, path, _):
    """
    Error handler for `shutil.rmtree`.
    If the error is due to an access error (read-only file), it attempts to
    add write permission and then retries the operation.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)

# Ensure app context for tasks that need current_app.config
@celery.task(bind=True)
def run_analysis_task(self, github_url: str, sector_hint: str, framework_hint: str, plan: str, user_token: str):
    app = current_app._get_current_object() # Access Flask app instance
    
    scan_id = str(uuid.uuid4())
    repo_name = github_url.split('/')[-1].replace('.git', '')
    repo_path = os.path.join(app.config['PULLED_CODE_DIR'], scan_id, repo_name)

    try:
        print(f"[Task:{self.request.id}] Received analysis request for scan {scan_id}")
        print(f"[Task:{self.request.id}] URL: {github_url}, Sector: {sector_hint}, Framework: {framework_hint}, Plan: {plan}")
        
        # Phase 1: Input & Analysis
        print(f"[Task:{self.request.id}] Phase 1: Cloning repository into isolated path: {repo_path}")
        github_service = GitHubService()
        github_service.clone_repository(github_url, repo_path)
        print(f"[Task:{self.request.id}] Repository cloned to: {repo_path}")
        
        # Phase 2: Data Processing & Storage
        print(f"[Task:{self.request.id}] Phase 2: Starting codebase analysis with plan: {plan}...")
        
        # Perform standard security analysis
        analysis_service = AnalysisService(plan=plan)
        scan_results = analysis_service.analyze_codebase(repo_path, sector_hint, scan_id)
        
        framework_analysis_results = None
        if framework_hint:
            print(f"[Task:{self.request.id}] Starting framework analysis for: {framework_hint}")
            try:
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
                    # Save framework analysis to its own file
                    filename = f"{scan_id}_EndpointAnalysis.json"
                    save_path = os.path.join(app.config['DATA_DIR'], "scanned_results", filename)
                    with open(save_path, 'w') as f:
                        json.dump(framework_analysis_results, f, indent=2)
                    print(f"[Task:{self.request.id}] Successfully saved framework analysis to {save_path}")

            except Exception as fw_e:
                print(f"[Task:{self.request.id}] Framework analysis failed: {str(fw_e)}")
                framework_analysis_results = {"error": str(fw_e)}

        # Return results needed for frontend polling
        return {
            'status': 'success',
            'scan_id': scan_results['scan_id'],
            'plan_used': plan,
            'total_findings': scan_results['summary']['total_findings'],
            'framework_analysis': framework_analysis_results, 
            'message': f'Analysis completed successfully using {plan} plan'
        }
    
    except Exception as e:
        print(f"[Task:{self.request.id}] ERROR for scan {scan_id}: {str(e)}")
        # Propagate exception so Celery marks task as FAILED
        raise
    
    finally:
        # Phase 3: Cleanup
        parent_dir = os.path.dirname(repo_path)
        if os.path.exists(parent_dir):
            print(f"[Task:{self.request.id}] Cleaning up directory: {parent_dir}")
            shutil.rmtree(parent_dir, onerror=_remove_readonly_onerror)

@celery.task(bind=True)
def generate_report_task(self, scan_id: str, report_type: str, user_token: str, model_name: str):
    app = current_app._get_current_object() # Access Flask app instance
    
    try:
        print(f"[Task:{self.request.id}] Report generation request for Scan ID: {scan_id}, Type: {report_type}, Model: {model_name}")

        report_service = ReportService()
        report_path = report_service.generate_report(scan_id, report_type, user_token, model_name)
        
        # The report_path here is a local file path within the worker's filesystem.
        # For true persistence and download, this should be uploaded to cloud storage.
        # For now, we'll return the local path, but this means the file is ephemeral.
        # User will need to download it immediately after task completion.

        return {
            'status': 'success',
            'report_path': report_path,
            'message': 'Report generated successfully.'
        }
    
    except Exception as e:
        print(f"[Task:{self.request.id}] ERROR during report generation: {str(e)}")
        raise # Propagate exception for Celery to mark task as FAILED
