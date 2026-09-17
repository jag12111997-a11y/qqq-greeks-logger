import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import qqq_greeks_logger_alpaca as logger
import publish_history as publisher

HEADER='run_time,expiration,strike,spot,delta,gamma\n'
A='2026-09-15 14:00:00,2026-09-15,700,705,-0.5,0.02\n'
B='2026-09-15 14:01:00,2026-09-15,700,706,-0.4,0.02\n'
CSV='data/2026-09-15/am/qqq_greeks_puts_2026-09-15_am.csv'

class HistoryPublishTests(unittest.TestCase):
    def test_logger_publishes_both_sides_and_keeps_running_on_failure(self):
        with patch.object(logger,'CI_PUSH',True), patch.object(logger,'output_path',side_effect=lambda k:'data/'+k+'.csv'), patch.object(publisher,'publish') as publish:
            logger.push_live_snapshots()
            self.assertIn('data/call.csv',publish.call_args.args[1])
            self.assertIn('data/put.csv',publish.call_args.args[1])
            publish.side_effect=RuntimeError('test failure')
            logger.push_live_snapshots()
    def test_local_runs_do_not_publish(self):
        with patch.object(logger,'CI_PUSH',False), patch.object(publisher,'publish') as publish:
            logger.push_live_snapshots();publish.assert_not_called()
    def test_conflicting_push_retries_without_touching_source(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}):
            root=Path(root)
            def git(*args,cwd=root):
                return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()
            git('init','--bare','remote.git');git('clone',str(root/'remote.git'),'source')
            source=root/'source';git('checkout','-b','main',cwd=source)
            git('config','user.name','Test',cwd=source);git('config','user.email','test@example.com',cwd=source)
            p=source/CSV;p.parent.mkdir(parents=True);p.write_text(HEADER+A)
            (source/'unrelated.txt').write_text('initial')
            git('add','.',cwd=source);git('commit','-m','initial',cwd=source);git('push','origin','main',cwd=source)
            git('clone','--branch','main',str(root/'remote.git'),'other')
            other=root/'other';git('config','user.name','Test',cwd=other);git('config','user.email','test@example.com',cwd=other)
            p.write_text(HEADER+A+B);(source/'unrelated.txt').write_text('uncommitted local edit')
            head=git('rev-parse','HEAD',cwd=source)
            real_run=subprocess.run; raced=[]
            def run(args,**kwargs):
                if args[:3]==['git','push','origin'] and not raced:
                    raced.append(True)
                    (other/'unrelated.txt').write_text('concurrent remote change')
                    for command in [['git','add','.'],['git','commit','-m','concurrent'],['git','push','origin','main']]:
                        real_run(command,cwd=other,check=True,capture_output=True)
                return real_run(args,**kwargs)
            with patch.object(publisher.subprocess,'run',side_effect=run):
                self.assertTrue(publisher.publish(source,[CSV]))
            self.assertEqual(git('rev-parse','HEAD',cwd=source),head)
            self.assertEqual((source/'unrelated.txt').read_text(),'uncommitted local edit')
            self.assertEqual(p.read_text(),HEADER+A+B)
            self.assertEqual(git('--git-dir='+str(root/'remote.git'),'show','main:'+CSV), (HEADER+A+B).strip())
            self.assertEqual(git('--git-dir='+str(root/'remote.git'),'show','main:unrelated.txt'),'concurrent remote change')
            self.assertFalse(publisher.publish(source,[CSV]))
