"""Offline regression: no WeChat clicks, sends, database access or model calls."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import aliases
import json_tables
import memory
import sticker_read
import watchdog_once as watchdog

# Load only specific function definitions: importing bot has startup side effects.
tree = ast.parse((BASE / 'bot.py').read_text(encoding='utf-8'))
names = {'_wx_audio_mute', '_wx_mute_state_items', '_video_cached_mp4',
         '_video_montage', '_video_audio_chunk', '_close_video_player',
         '_cleanup_video_temps'}
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]


class Regression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = dict(os=os, json=json, time=time, Path=Path, CFG={},
                        log=lambda *a: None)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'isolated_bot', 'exec'), self.env)
        self.alias_state = (aliases._path, copy.deepcopy(aliases._cache), aliases.persist['on'])
        self.mem_state = (memory._path, memory._data)
        self.addCleanup(self.restore)
        self.addCleanup(self.tmp.cleanup)

    def restore(self):
        aliases._path, aliases._cache, aliases.persist['on'] = self.alias_state
        memory._path, memory._data = self.mem_state

    def table(self, name='table.json', value=None):
        p = self.root / name
        p.write_text(json.dumps(value or {}), encoding='utf-8')
        return p

    def test_table_cache_reads_once(self):
        p = self.table(value={'x': {'items': [1]}})
        with patch.object(json_tables.json, 'load', wraps=json.load) as load:
            for _ in range(30):
                self.assertEqual(json_tables.read_table(p)['x']['items'], [1])
            self.assertEqual(load.call_count, 1)

    def test_table_deepcopy(self):
        p = self.table(value={'x': [1]})
        json_tables.read_table(p)['x'].append(2)
        self.assertEqual(json_tables.read_table(p), {'x': [1]})

    def test_table_hot_reload_and_corrupt(self):
        p = self.table(value={'x': 1})
        json_tables.read_table(p)
        p.write_text('{"x":22}', encoding='utf-8')
        # Windows 写入时间可能延迟刷新；显式改变时间，避免同尺寸重写测试抖动。
        stamp = p.stat().st_mtime_ns + 1_000_000_000
        os.utime(p, ns=(stamp, stamp))
        self.assertEqual(json_tables.read_table(p), {'x': 22})
        p.write_text('{broken', encoding='utf-8')
        self.assertEqual(json_tables.read_table(p), {'x': 22})

    def test_atomic_write_failure_preserves_original(self):
        p = self.table(value={'old': 1})
        before = p.read_bytes()
        with patch.object(json_tables.os, 'replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                json_tables.write_table(p, {'new': 2})
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_concurrent_sticker_bind(self):
        p = self.table()
        with patch.object(sticker_read, 'MAP_PATH', p):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda n: sticker_read.bind_md5_label('%032x' % n, str(n)), range(40)))
            self.assertEqual(len(sticker_read.load_md5_map()), 40)

    def test_alias_corruption_not_overwritten(self):
        p = self.table(value={'global': {'aliases': {'test': 'person'}}})
        aliases.set_path(p, persist_too=True)
        old = copy.deepcopy(aliases._load())
        for bad in ('{broken', '[]'):
            p.write_text(bad, encoding='utf-8')
            self.assertEqual(aliases._load(), old)
            self.assertFalse(aliases.remember('group', 'newname', 'wxid_test'))
            with self.assertRaises(ValueError):
                aliases.block('group', 'newname')
            self.assertEqual(p.read_text(encoding='utf-8'), bad)

    def test_alias_nested_bad_data(self):
        p = self.table(value={'global': [], 'groups': {'g': []}, 'auto': {'bot': {'g': 3}}})
        aliases.set_path(p)
        self.assertEqual(aliases.bot_names('g'), [])
        self.assertEqual(aliases._blocked(aliases._load(), 'g'), set())

    def test_alias_switch_has_no_old_cache(self):
        aliases.set_path(self.table(value={'old': 1}))
        aliases._load()
        aliases.set_path(self.root / 'missing.json')
        self.assertEqual(aliases._load(), {})

    def test_bad_memory_records(self):
        p = self.table(value={'people': {'bad': [], 'ok': {'ts': 'bad', 'facts':
            [None, 3, {'t': 'likes fish', 'ts': 'NaN'}]}}})
        memory.set_path(p)
        data = memory._load()
        self.assertNotIn('bad', data['people'])
        self.assertEqual(data['people']['ok']['facts'][0]['ts'], 0)
        memory._prune(data, time.time())

    def test_watchdog_bad_history(self):
        now = time.time()
        p = self.table(value={'starts': [None, {}, 'bad', 'NaN', now+30, now-5, now-900]})
        with patch.object(watchdog, 'STATE_PATH', str(p)):
            self.assertEqual(watchdog.load_state()['starts'], [now-5])

    def test_watchdog_project_match(self):
        def proc(pid, directory, name='python.exe', arg='bot.py'):
            return types.SimpleNamespace(info={'pid': pid, 'name': name,
                'cmdline': ['python', arg]}, cwd=lambda: directory)
        with patch.object(watchdog.psutil, 'process_iter', return_value=[
            proc(1, str(self.root)), proc(2, watchdog.BASE, 'pythonw.exe')]):
            self.assertEqual(watchdog.bot_pid(), 2)

    def audio(self, initial=(False, True)):
        class Volume:
            def __init__(self, value):
                self.value = value
                self.fail_mute = self.fail_restore = False
            def GetMute(self):
                return self.value
            def SetMute(self, value, context):
                if (value and self.fail_mute) or (not value and self.fail_restore):
                    raise OSError('audio failure')
                self.value = bool(value)
        sessions = [types.SimpleNamespace(ProcessId=42, SimpleAudioVolume=Volume(v)) for v in initial]
        self.env['_wechat_audio_sessions'] = lambda: sessions
        p = self.root / 'mute.json'
        self.env['_wechat_mute_state_path'] = lambda: str(p)
        return sessions, p

    def test_mute_restore_real_interface(self):
        sessions, p = self.audio()
        self.assertTrue(self.env['_wx_audio_mute'](True))
        self.assertTrue(all(s.SimpleAudioVolume.GetMute() for s in sessions))
        self.assertTrue(self.env['_wx_audio_mute'](False))
        self.assertEqual([s.SimpleAudioVolume.GetMute() for s in sessions], [False, True])
        self.assertFalse(p.exists())

    def test_snapshot_failure_never_mutes(self):
        sessions, p = self.audio()
        with patch('builtins.open', side_effect=OSError('disk full')):
            self.assertFalse(self.env['_wx_audio_mute'](True))
        self.assertEqual([s.SimpleAudioVolume.GetMute() for s in sessions], [False, True])

    def test_partial_mute_failure_restores(self):
        sessions, p = self.audio((False, False))
        sessions[1].SimpleAudioVolume.fail_mute = True
        self.assertFalse(self.env['_wx_audio_mute'](True))
        self.assertFalse(sessions[0].SimpleAudioVolume.GetMute())

    def test_restore_failure_keeps_marker(self):
        sessions, p = self.audio((False,))
        self.env['_wx_audio_mute'](True)
        sessions[0].SimpleAudioVolume.fail_restore = True
        self.assertFalse(self.env['_wx_audio_mute'](False))
        self.assertTrue(p.exists())
        sessions[0].SimpleAudioVolume.fail_restore = False
        self.assertTrue(self.env['_wx_audio_mute'](False))
        self.assertFalse(p.exists())

    def test_exact_video_no_guess(self):
        self.table('unrelated.mp4', {'content': 'x'*10000})
        self.env['_video_cache_path'] = lambda *a: ''
        self.assertEqual(self.env['_video_cached_mp4']({}, 'g'), '')

    def test_failed_ffmpeg_discards_stale_outputs(self):
        self.env.update(VIDEO_DIR=self.root, _ffmpeg_exe=lambda: 'ffmpeg',
                        _run_ffmpeg=lambda *a, **kw: types.SimpleNamespace(returncode=1))
        for suffix, name, args in [('_frames.jpg', '_video_montage', ('v.mp4', 120)),
                                   ('_audio.wav', '_video_audio_chunk', ('v.mp4',))]:
            p = self.root / ('v' + suffix)
            p.write_bytes(b'x'*1000)
            self.assertEqual(self.env[name](*args), '')
            self.assertFalse(p.exists())

    def test_player_close_retries(self):
        calls = []
        self.env['_video_player_windows'] = lambda: [7, 9]
        self.env['_user32'] = lambda: types.SimpleNamespace(PostMessageW=lambda h, *a: calls.append(h))
        with patch.object(time, 'sleep'), patch.object(time, 'monotonic', side_effect=[0, 0, 1]):
            self.assertTrue(self.env['_close_video_player']({7}, wait=.5))
        self.assertNotIn(7, calls)
        self.assertGreaterEqual(calls.count(9), 2)

    def test_cleanup_leaves_unknown_files(self):
        self.env['VIDEO_DIR'] = self.root
        for name in ('owned.wav', 'notes.txt'):
            (self.root/name).write_bytes(b'test')
            os.utime(self.root/name, (1, 1))
        self.env['_cleanup_video_temps'](1)
        self.assertFalse((self.root/'owned.wav').exists())
        self.assertTrue((self.root/'notes.txt').exists())

    def test_video_pipeline_exception_cleanup(self):
        source = (BASE/'bot.py').read_text(encoding='utf-8')
        start = source.index('        video_path = None\n', source.index('    elif mtype == "视频":'))
        end = source.index('        quote = None\n', start)
        snippet = 'def pipeline():\n' + ''.join('    '+line[8:]+'\n' for line in source[start:end].splitlines())
        for stage in ('_video_montage', '_video_audio_text', 'ask_ai'):
            with self.subTest(stage=stage):
                video = self.root/'sample.mp4'
                image = self.root/'sample.jpg'
                video.write_bytes(b'video')
                image.write_bytes(b'image')
                deleted = []
                env = dict(self.env, VIDEO_DIR=self.root, meta={}, msg={}, chat='g', nick='n',
                    wxid='w', round_extra='', download_video=lambda *a: str(video),
                    _video_too_big=lambda *a: False, _video_duration=lambda *a: 10,
                    _video_montage=lambda *a: str(image),
                    _video_audio_text=lambda *a: '', _video_prompt=lambda *a: '',
                    _video_delete_cache=lambda *a: deleted.append(True), ask_ai=lambda *a, **k: ('ok', None))
                def fail(*a, **k):
                    raise RuntimeError('injected failure')
                env[stage] = fail
                exec(compile(snippet, 'pipeline', 'exec'), env)
                with self.assertRaises(RuntimeError):
                    env['pipeline']()
                self.assertFalse(video.exists())
                self.assertEqual(deleted, [True])
                if stage != '_video_montage':
                    self.assertFalse(image.exists())

    def test_long_video_sampling_covers_duration(self):
        captured = []
        self.env.update(VIDEO_DIR=self.root, _ffmpeg_exe=lambda: 'ffmpeg',
            _run_ffmpeg=lambda args, **k: captured.extend(args))
        self.env['_video_montage']('v.mp4', 3600)
        filt = captured[captured.index('-vf')+1]
        rate = float(filt.split(',')[0].split('=')[1])
        self.assertAlmostEqual(rate * 3600, 12, places=3)

    def test_gdi_resources_released_on_exception(self):
        import ctypes
        import sticker_sender
        calls = []
        class Function:
            def __init__(self, name, result):
                self.name, self.result = name, result
            def __call__(self, *args):
                calls.append(self.name)
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result
        user = types.SimpleNamespace(**{n: Function(n, r) for n, r in
            [('GetWindowDC', 1), ('ReleaseDC', 1), ('PrintWindow', RuntimeError('draw error'))]})
        gdi = types.SimpleNamespace(**{n: Function(n, r) for n, r in
            [('CreateCompatibleDC', 2), ('CreateCompatibleBitmap', 3), ('SelectObject', 4),
             ('GetDIBits', 1), ('DeleteObject', 1), ('DeleteDC', 1)]})
        with patch.object(ctypes, 'windll', types.SimpleNamespace(user32=user)), \
                patch.object(sticker_sender, '_GDI', (user, gdi)):
            with self.assertRaises(RuntimeError):
                sticker_sender._printwindow(1, (0, 0, 100, 100))
        self.assertEqual(calls[-3:], ['DeleteObject', 'DeleteDC', 'ReleaseDC'])

    def test_watchdog_mutex_real_windows(self):
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        name = 'Local\\chatbot-regression-%s' % os.getpid()
        first = kernel.CreateMutexW(None, False, name)
        second = kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        try:
            self.assertTrue(first and second)
            self.assertEqual(error, 183)
        finally:
            kernel.CloseHandle(second)
            kernel.CloseHandle(first)


if __name__ == '__main__':
    unittest.main(verbosity=2)
