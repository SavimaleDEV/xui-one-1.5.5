import hashlib,importlib.util,json,os,re,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'updates'))
from xui_resources import budgets

class InstallerTests(unittest.TestCase):
 def test_budgets_fit_small_and_large_machines(self):
  for ram in [4096,8192,16384,32768,65536,128000,262144]:
   for pools in [1,4,6,12]:
    p=budgets(ram,2,pools)
    self.assertLessEqual(p['xui_mb']+p['mysql_mb']+p['os_reserve_mb']+p['proxy_mb'],ram)
    self.assertLess(p['redis_mb'],p['xui_mb']);self.assertLessEqual(p['php_children'],48)
    self.assertGreaterEqual(p['php_children'],1);self.assertLessEqual(p['mysql_connections'],1024)
  with self.assertRaises(ValueError):budgets(2048,1,4)

 def test_stage_is_idempotent_and_keeps_credentials_and_native_php(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   with tarfile.open(ROOT/'xui.tar.gz') as archive:
    for member in archive:
     name=str(Path(member.name))
     needed=(name.startswith('bin/nginx/conf/') or name.startswith('bin/php/etc/') or name in ['admin/post.php','admin/assets/js/listings.js'])
     if needed and member.isfile():
      p=root/'home/xui'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(archive.extractfile(member).read())
   codes=root/'home/xui/bin/nginx/conf/codes'
   for p in codes.glob('*.conf'):p.unlink()
   template=(codes/'template').read_text()
   for key,value in {'#CODE#':'testadmin','#TYPE#':'admin','#WHITELIST#':'','#BURST#':'500'}.items():template=template.replace(key,value)
   (codes/'testadmin.conf').write_text(template)
   for name,text in {'etc/mysql/my.cnf':'# custom\n[mysqld]\nmax_connections=8192\n','etc/logrotate.d/mysql-server':'/var/log/mysql/mysql.log {\n daily\n rotate 7\n}\n','home/xui/bin/redis/redis.conf':'bind 127.0.0.1\nrequirepass KEEP_THIS_SECRET\nmaxmemory-policy noeviction\n','home/xui/config/config.ini':'[XUI]\nhostname="127.0.0.1"\ndatabase="xui"\nserver_id=1\n[Encrypted]\nusername="PRESERVE"\npassword="PRESERVE"\n'}.items():
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
   native=hashlib.sha256((root/'home/xui/admin/post.php').read_bytes()).hexdigest()
   args=[sys.executable,str(ROOT/'updates/apply.py'),'--root',str(root),'--stage','--memory-mb','8192','--cpus','2']
   a=subprocess.run(args+['--fresh'],capture_output=True,text=True);self.assertEqual(a.returncode,0,a.stderr)
   snapshot={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and '/var/backups/' not in str(p)}
   b=subprocess.run(args,capture_output=True,text=True);self.assertEqual(b.returncode,0,b.stderr)
   after={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and '/var/backups/' not in str(p)}
   self.assertEqual({k:hashlib.sha256(v).hexdigest() for k,v in snapshot.items()},{k:hashlib.sha256(v).hexdigest() for k,v in after.items()})
   self.assertEqual(json.loads(b.stdout)['changed'],0)
   self.assertEqual(hashlib.sha256((root/'home/xui/admin/post.php').read_bytes()).hexdigest(),native)
   self.assertIn('PRESERVE',(root/'home/xui/config/config.ini').read_text())
   redis=(root/'home/xui/bin/redis/redis.conf').read_text();self.assertIn('KEEP_THIS_SECRET',redis);self.assertIn('maxmemory-policy noeviction',redis)
   self.assertIn('/var/lib/xui-mass-queue',(root/'home/xui/custom-mass-queue/lib.php').read_text())
   q=(root/'etc/systemd/system/xui-mass-queue.service').read_text();self.assertIn('User=xui-queue',q);self.assertIn('Before=xuione.service',q)
   self.assertIn('ExecStartPre=',(root/'etc/systemd/system/xuione.service.d/90-xui-stability.conf').read_text())
   self.assertIn('admin:stream_mass',(codes/'template').read_text())
   self.assertIn('/home/xui/#TYPE#/post.php',(codes/'template').read_text())
   profile=json.loads((root/'etc/xui-stability/profile.json').read_text());self.assertEqual(profile['memory_mb'],8192)
   # Native-generated future aliases remain valid and are not given duplicate handlers.
   future=(codes/'template').read_text()
   for k,v in {'#CODE#':'future','#TYPE#':'admin','#WHITELIST#':'','#BURST#':'500'}.items():future=future.replace(k,v)
   (codes/'future.conf').write_text(future)
   c=subprocess.run(args,capture_output=True,text=True);self.assertEqual(c.returncode,0,c.stderr)
   self.assertEqual((codes/'future.conf').read_text().count('location = /future/post.php'),1)

 def test_watchdog_no_source_machine_ips(self):
  text=(ROOT/'updates/xui-mariadb-watchdog').read_text()
  self.assertIn('if not local_database():',text)
  r=subprocess.run([sys.executable,str(ROOT/'updates/xui-mariadb-watchdog'),'--self-test'],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)

 def test_installer_has_no_destructive_reinstall_or_password_argv(self):
  source=(ROOT/'install').read_text()
  self.assertNotIn('DROP DATABASE',source);self.assertNotIn('killall',source)
  self.assertNotIn('apt-key',source);self.assertNotIn('lock-frontend',source)
  self.assertNotIn('mysql -p',source);self.assertIn('os.O_EXCL',source)
  self.assertEqual(source.count("command(['systemctl','start','xuione.service'])"),1)


 def test_ubuntu_versions_and_compatibility_profiles(self):
  from ubuntu_compat import release,packages,prepare
  import ast
  node=next(n for n in ast.parse((ROOT/'install').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PACKAGES' for t in n.targets))
  common=ast.literal_eval(node.value)
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);osrelease=root/'os-release'
   for version in ['20.04','22.04','24.04']:
    osrelease.write_text('ID=ubuntu\nVERSION_ID="'+version+'"\n');self.assertEqual(release(osrelease),version)
    selected=packages(common,version)
    self.assertEqual('libssl1.1' in selected,version=='20.04')
    self.assertEqual('nginx-full' in selected,version!='20.04')
    if version=='24.04':self.assertIn('libcurl4t64',selected)
   osrelease.write_text('ID=debian\nVERSION_ID="12"\n')
   with self.assertRaises(RuntimeError):release(osrelease)
   for directory,binary in [('nginx','nginx'),('nginx_rtmp','nginx_rtmp')]:
    path=root/'home/xui/bin'/directory/'sbin'/binary;path.parent.mkdir(parents=True);path.write_bytes(b'VENDOR')
    conf=path.parent.parent/'conf/nginx.conf';conf.parent.mkdir();conf.write_text('events {}\nhttp {}\n')
   (root/'home/xui/config').mkdir()
   prepare('24.04',ROOT/'components',root,execute=False)
   first={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
   prepare('24.04',ROOT/'components',root,execute=False)
   self.assertEqual(first,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})
   self.assertEqual((root/'home/xui/bin/nginx/sbin/nginx.vendor-original').read_bytes(),b'VENDOR')

if __name__=='__main__':unittest.main(verbosity=2)
