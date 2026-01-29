import os
import json
import uuid
import shutil
import stat
from datetime import datetime
from flask import current_app
import logging

from analysis_engine.orchestrator import AnalysisOrchestrator
from app.services.repo_info_service import RepoInfoExtractor

logger = logging.getLogger(__name__)


class AnalysisService:
    """
    Thin service layer.
    Delegates all analysis to AnalysisOrchestrator.
    """

    def __init__(self, plan='basic'):
        self.data_dir = current_app.config['DATA_DIR']
        self.plan = plan

        self.repo_extractor = RepoInfoExtractor()
        self.orchestrator = AnalysisOrchestrator(plan=plan)

        logger.info(f"AnalysisService initialized (delegating to orchestrator), plan={plan}")

    def analyze_codebase(self, repo_path, sector_hint, scan_id):
        try:
            logger.info(f"🔍 Starting scan {scan_id} on path {repo_path}")

            # 1️⃣ Extract repository context
            repo_info = self.repo_extractor.extract(repo_path)

            # 2️⃣ Run FULL analysis via orchestrator
            findings, metrics = self.orchestrator.run(
                repo_path=repo_path,
                repository_info=repo_info
            )
            
            risk_summary = metrics.pop('llm_risk_summary', 'Not generated.')
            summary = {
                "total_findings": metrics.get("total_findings", 0),
                "by_severity": metrics.get("by_severity", {}),
                "analysis_time": metrics.get("total_time", 0)
            }

            # 3️⃣ Build final scan object
            scan_results = {
                "scan_id": scan_id,
                "timestamp": datetime.now().isoformat(),
                "repository_path": repo_path,
                "sector_hint": sector_hint,
                "plan_used": self.plan,
                "repository_info": repo_info,
                "findings": findings,
                "summary": summary,
                "risk_summary": risk_summary,
                "metrics": metrics
            }

            # 4️⃣ Persist results
            self._save_scan_results(scan_id, scan_results)

            logger.info(f"✅ Scan complete: {len(findings)} findings")
            return scan_results

        except Exception as e:
            logger.error(f"❌ Analysis failed for scan {scan_id}: {e}", exc_info=True)
            raise

    def _save_scan_results(self, scan_id, results):
        results_dir = os.path.join(self.data_dir, "scanned_results")
        os.makedirs(results_dir, exist_ok=True)

        path = os.path.join(results_dir, f"{scan_id}.json")
        with open(path, "w") as f:
            json.dump(results, f, indent=2)

        logger.info(f"💾 Results saved: {path}")

    @staticmethod
    def _remove_readonly_onerror(func, path, _):
        """
        Error handler for `shutil.rmtree`.
        If the error is due to an access error (read-only file), it attempts to
        add write permission and then retries the operation.
        """
        os.chmod(path, stat.S_IWRITE)
        func(path)
