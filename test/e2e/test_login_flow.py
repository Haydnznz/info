"""Playwright smoke test for authentication and report-file navigation."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.getenv('E2E_BASE_URL', 'http://127.0.0.1:9000')


def seed_report(data_root: Path) -> None:
    report_id = 'Rplaywright-smoke'
    report_dir = data_root / 'reports' / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / 'report.md').write_text('# Playwright 冒烟报告\n\n浏览器报告文件链路校验。\n', encoding='utf-8')
    (report_dir / 'sources.json').write_text(json.dumps({
        'query': 'Playwright 冒烟主题',
        'executed_at': '2026-01-01T00:00:00+08:00',
        'sources': [],
    }, ensure_ascii=False), encoding='utf-8')
    (report_dir / 'metadata.json').write_text(json.dumps({
        'status': '待核验', 'risk_level': '低', 'confidence': 'high', 'summary': '浏览器回归测试报告',
    }, ensure_ascii=False), encoding='utf-8')


def wait_for_server(process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'API process exited with status {process.returncode}')
        try:
            with urlopen(BASE_URL + '/api/health', timeout=2):
                return
        except (OSError, URLError):
            time.sleep(0.25)
    raise TimeoutError('API server did not become ready')


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='info-playwright-') as temp:
        data_root = Path(temp)
        seed_report(data_root)
        env = dict(os.environ)
        env.update({
            'AUTH_ENABLED': 'true',
            'AUTH_SECRET': 'github-actions-playwright-secret',
            'AUTH_ADMIN_USERNAME': 'admin',
            'AUTH_ADMIN_PASSWORD': 'playwright-admin-password',
            'AUTH_OPERATOR_USERNAME': 'operator',
            'AUTH_OPERATOR_PASSWORD': 'playwright-operator-password',
            'AUTH_VIEWER_USERNAME': 'viewer',
            'AUTH_VIEWER_PASSWORD': 'playwright-viewer-password',
            'OPINION_WORKER_TOKEN': 'github-actions-worker-secret',
            'OPINION_DATA_DIR': str(data_root),
            'MONGO_ENABLED': 'false',
            'MILVUS_ENABLED': 'false',
            'LLM_ENABLED': 'false',
        })
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'web.fastapi_app:app', '--host', '127.0.0.1', '--port', '9000'],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_server(process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context()
                page = context.new_page()
                # ``page.goto`` follows the API's 307 redirect. Assert the
                # final URL directly instead of waiting for a second
                # navigation event, which is timing-sensitive in CI.
                page.goto(BASE_URL + '/', wait_until='domcontentloaded')
                assert re.search(r'/login(?:\?.*)?$', page.url), page.url
                page.get_by_label('用户名').fill('admin')
                page.get_by_label('密码', exact=True).fill('playwright-admin-password')
                page.get_by_role('button', name='登录').click()
                page.wait_for_url(re.compile(r'/+$'), wait_until='domcontentloaded')
                page.get_by_role('heading', name='控制台').wait_for(timeout=15000)

                page.get_by_role('button', name='报告管理').click()
                page.get_by_text('Playwright 冒烟主题').wait_for(timeout=15000)
                with context.expect_page() as report_page_info:
                    page.get_by_role('link', name='查看报告').click()
                report_page = report_page_info.value
                report_page.wait_for_load_state('domcontentloaded')
                assert '/report-file/Rplaywright-smoke' in report_page.url
                assert 'Playwright 冒烟报告' in report_page.locator('body').inner_text()
                report_page.close()

                page.get_by_role('button', name='退出登录').click()
                page.wait_for_url(re.compile(r'/login(?:\?.*)?$'), wait_until='domcontentloaded')
                page.get_by_role('heading', name='登录工作台').wait_for()
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
