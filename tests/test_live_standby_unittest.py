"""Verifica leitores disponíveis sem depender de TikTok ou do modelo de voz."""
import tempfile
import unittest
from pathlib import Path

from celia.live_mode.manager import LiveModeManager
from celia.live_mode.settings import SettingsStore


class StandbyReaders(unittest.IsolatedAsyncioTestCase):
    async def test_companions_read_with_empty_or_occupied_seats(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = SettingsStore(Path(tmp) / 'settings.json')
            settings.update({'per_user_interval_s': 0, 'per_user_max_per_minute': 30})
            manager = LiveModeManager(settings, state_dir=Path(tmp))
            manager.mode = 'test'
            top = []
            manager._top_user_ids = lambda: top
            async def chat(number):
                return await manager.ingest({'type': 'chat', 'origin': 'simulated', 'msg_id': str(number),
                                             'user_id': 'viewer'+str(number), 'nickname': 'Pessoa', 'text': 'Olá pessoal'})
            await chat(1)
            self.assertEqual([(j.character,j.reason) for j in manager._queue], [('slime','reading'),('celia','default')])
            manager._queue.clear()
            await chat(2)
            self.assertEqual(manager._queue[0].character, 'ursinho')
            manager._queue.clear()
            top.append('top1')
            await chat(3)
            self.assertEqual(manager._queue[0].character, 'slime')
            manager._queue.clear()
            top.append('top2')
            await chat(4)
            self.assertEqual([(j.character,j.reason) for j in manager._queue], [('ursinho','reading'),('celia','default')])

    async def test_busy_queue_keeps_reading_with_top_two_occupied(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = SettingsStore(Path(tmp) / 'settings.json')
            settings.update({'per_user_interval_s': 0, 'queue_max': 4})
            manager = LiveModeManager(settings, state_dir=Path(tmp))
            manager.mode = 'test'
            manager._top_user_ids = lambda: ['donor1', 'donor2']
            for i in range(8):
                result = await manager.ingest({'type':'chat','origin':'simulated','msg_id':str(i),
                    'user_id':'viewer'+str(i),'nickname':'Pessoa','text':'Olá pessoal '+str(i)})
                self.assertTrue(result['ok'])
                self.assertLessEqual(len(manager._queue), 4)
            self.assertEqual({j.character for j in manager._queue if j.reason=='reading'}, {'slime','ursinho'})
            self.assertEqual(manager._queue[-1].character, 'celia')


if __name__ == '__main__':
    unittest.main()
