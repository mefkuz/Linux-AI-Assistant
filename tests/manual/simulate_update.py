"""Offline release simulation: real Qt dialogs and local Git, no user installation changes."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('PYNPUT_BACKEND', 'dummy')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QWidget
from src.core import updater
from src.core.version import __version__
from src.gui.app import AppManager, Communicate, SettingsWindow


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    report = {}
    payload = {'tag_name': 'v' + __version__, 'body': (
        '## Yenilikler\n- Codex entegrasyonu ve sesli soru yanıtları.\n'
        '- AI görsellerinde daire, kutu ve ok ile işaretleme.\n'
        '- Sade soru penceresi ve tek ekran yakalama.\n'
        '- Güncelleme sonucunun arayüze aktarılması düzeltildi.'),
        'html_url': updater.HTML_RELEASES + '/tag/v' + __version__}
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read = Mock(return_value=json.dumps(payload).encode())
    with patch.object(updater.urllib.request, 'urlopen', return_value=response):
        info = updater.check_for_updates(current='1.6.0')
        assert info['update_available'] and info['latest_version'] == __version__
        assert not updater.check_for_updates(current=__version__)['update_available']
    report['old_and_current_version_detection'] = 'PASS'
    manager = AppManager.__new__(AppManager)
    class Settings(dict):
        def set(self, key, value): self[key] = value
    manager.settings = Settings()
    manager.comm = Communicate()
    manager.comm.update_finished.connect(manager._after_update, Qt.ConnectionType.QueuedConnection)
    manager._restart_app = Mock()
    manager.tray = Mock()
    errors = []
    def interact(action):
        try:
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QDialog)
            dialog.grab().save(str(output / ('update-' + action + '.png')))
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == action)
            button.click()
        except Exception as exc:
            errors.append(str(exc))
            if app.activeModalWidget(): app.activeModalWidget().reject()
    for action in ('Daha Sonra', 'Bu Sürümü Atla'):
        QTimer.singleShot(0, lambda action=action: interact(action))
        manager._show_update_dialog(info)
        assert not errors, errors
    assert manager.settings['skipped_update_version'] == payload['tag_name']
    with patch.object(manager, '_show_update_dialog') as show:
        manager._on_update_checked(info, True)
        show.assert_not_called()
        manager._on_update_checked(info, False)
        show.assert_called_once()
    report['later_skip_and_manual_override'] = 'PASS'
    timed_out = []
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: (timed_out.append(True), app.activeModalWidget().reject()))
    watchdog.start(5000)
    with patch.object(updater, 'perform_update', return_value={'ok': True, 'restart_needed': True, 'message': 'Güncelleme tamamlandı.'}) as install, \
            patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No) as restart:
        QTimer.singleShot(0, lambda: interact('Şimdi Güncelle'))
        manager._show_update_dialog(info)
        assert not errors and not timed_out, (errors, timed_out)
        install.assert_called_once()
        restart.assert_called_once()
        assert manager.settings['skipped_update_version'] == ''
        manager._restart_app.assert_not_called()
    watchdog.stop()
    report['background_completion_and_restart_prompt'] = 'PASS'
    settings_window = SettingsWindow.__new__(SettingsWindow)
    QWidget.__init__(settings_window)
    settings_window.upd_status_lbl = QLabel()
    settings_window.upd_now_btn = QPushButton()
    settings_window.update_result.connect(settings_window._manual_update_result, Qt.ConnectionType.QueuedConnection)
    with patch.object(updater, 'check_for_updates', return_value=info):
        settings_window._manual_update_check()
        deadline = time.monotonic() + 3
        while not settings_window.upd_now_btn.isEnabled() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert settings_window.upd_now_btn.isEnabled()
        assert __version__ in settings_window.upd_status_lbl.text()
    report['settings_background_check'] = 'PASS'
    real_run = subprocess.run
    def git(*args):
        return real_run(['git', *map(str, args)], check=True, capture_output=True, text=True)
    with tempfile.TemporaryDirectory(prefix='linux-ai-update-sim-') as folder:
        base = Path(folder)
        remote, author, installed = [base / name for name in ('remote.git', 'author', 'installed')]
        git('init', '--bare', '--initial-branch=master', remote)
        git('clone', remote, author)
        git('-C', author, 'config', 'user.name', 'Simulation')
        git('-C', author, 'config', 'user.email', 'simulation@example.invalid')
        (author / 'version.txt').write_text('1.6.0')
        (author / '.gitignore').write_text('settings.json\nLoglar/\nvenv/\n')
        (author / 'requirements.txt').write_text('# Synthetic simulation: no packages installed\n')
        git('-C', author, 'add', '.')
        git('-C', author, 'commit', '-m', 'Old version fixture')
        git('-C', author, 'push', 'origin', 'master')
        git('clone', remote, installed)
        (installed / 'settings.json').write_text('{"synthetic_setting":true}')
        (installed / 'Loglar').mkdir()
        (installed / 'Loglar' / 'synthetic.txt').write_text('preserve me')
        (author / 'version.txt').write_text(__version__)
        (author / 'annotation-feature.txt').write_text('Synthetic new feature')
        git('-C', author, 'add', '.')
        git('-C', author, 'commit', '-m', 'New version fixture')
        git('-C', author, 'push', 'origin', 'master')
        pip_calls = []
        def command(cmd, **kwargs):
            if len(cmd) > 2 and cmd[1:3] == ['-m', 'pip']:
                pip_calls.append(cmd)
                return Mock(returncode=0, stdout='', stderr='')
            return real_run(cmd, **kwargs)
        with patch.object(updater.subprocess, 'run', side_effect=command):
            result = updater.perform_update(str(installed))
            assert result['ok'] and result['restart_needed'], result
            assert (installed / 'version.txt').read_text() == __version__
            assert (installed / 'annotation-feature.txt').is_file()
            assert (installed / 'settings.json').read_text() == '{"synthetic_setting":true}'
            assert (installed / 'Loglar' / 'synthetic.txt').read_text() == 'preserve me'
            assert len(pip_calls) == 1
            result = updater.perform_update(str(installed))
            assert result['ok'] and not result['restart_needed']
            (installed / 'version.txt').write_text('local modification')
            (author / 'version.txt').write_text('future fixture')
            git('-C', author, 'commit', '-am', 'Conflicting fixture')
            git('-C', author, 'push', 'origin', 'master')
            result = updater.perform_update(str(installed))
            assert not result['ok'] and (installed / 'version.txt').read_text() == 'local modification'
        zip_dir = base / 'zip-install'
        zip_dir.mkdir()
        assert not updater.perform_update(str(zip_dir))['ok']
    report['real_git_upgrade_preserves_user_files'] = 'PASS'
    report['dependency_refresh_command_without_installing'] = 'PASS'
    report['already_current_dirty_checkout_and_zip'] = 'PASS'
    report['simulated_upgrade'] = '1.6.0 -> ' + __version__
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(tempfile.mkdtemp(prefix='linux-ai-update-preview-')))
    run(parser.parse_args().output)
