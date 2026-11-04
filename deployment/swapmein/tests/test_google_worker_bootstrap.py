from pathlib import Path
import hashlib
import io
import tarfile
import pytest
from self_hosted_worker import google_bootstrap as bootstrap

def archive(entries):
 output=io.BytesIO()
 with tarfile.open(fileobj=output,mode='w:gz') as tar:
  for name,kind in entries:
   info=tarfile.TarInfo(name)
   if kind=='symlink':info.type=tarfile.SYMTYPE;info.linkname='/etc/shadow';tar.addfile(info)
   else:info.size=1;tar.addfile(info,io.BytesIO(b'x'))
 return output.getvalue()

def test_release_extracts_only_expected_package(tmp_path):
 bootstrap.extract_release(archive([('self_hosted_worker/runner.py','file'),('self_hosted_worker/templates/encode.json','file')]),tmp_path)
 assert (tmp_path/'self_hosted_worker/runner.py').read_bytes()==b'x'

@pytest.mark.parametrize('entry',[('../outside','file'),('/outside','file'),('other/runner.py','file'),('self_hosted_worker/link','symlink')])
def test_release_rejects_unsafe_entries_before_any_write(tmp_path,entry):
 with pytest.raises(ValueError):bootstrap.extract_release(archive([('self_hosted_worker/runner.py','file'),entry]),tmp_path)
 assert list(tmp_path.iterdir())==[]

def test_release_rejects_duplicate_overwrite(tmp_path):
 with pytest.raises(ValueError):bootstrap.extract_release(archive([('self_hosted_worker/runner.py','file')]*2),tmp_path)

def test_verified_download_checks_hash(monkeypatch):
 monkeypatch.setattr(bootstrap,'authenticated_bytes',lambda url,limit:b'code')
 assert bootstrap.verified_download('gs://private/immutable/worker.tar.gz',hashlib.sha256(b'code').hexdigest(),10)==b'code'
 with pytest.raises(ValueError):bootstrap.verified_download('gs://private/immutable/worker.tar.gz','0'*64,10)

def test_configuration_keeps_native_google_profile_and_original_models():
 config,profile=bootstrap.google_configuration('https://example.run.app')
 assert config['route']=='google_h3' and config['worker_id']=='google-character-swap-1'
 assert config['model_paths']['text'].endswith('/models/text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors')
 assert 'loader_variants' not in profile and profile['whitelist']==['recastfx_worker_nodes']
 assert all(command[1:3]==['-m','self_hosted_worker.launcher'] for command in config['launch_commands'].values())

def test_startup_embeds_hash_pins_and_clean_shutdown():
 script=bootstrap.startup_script('a'*64,'b'*64)
 assert 'RECASTFX_PINNED_WORKER_RELEASE_SHA256='+'a'*64 in script
 assert 'RECASTFX_PINNED_WORKER_CONFIG_SHA256='+'b'*64 in script
 assert '/sbin/shutdown -h now' in script and 'pip install' not in script

@pytest.mark.parametrize('uri',['https://example.com/archive','gs://private/obj?version=latest','gs://private/','gs://name:password@host/file'])
def test_gcs_artifacts_reject_noncanonical_locations(uri):
 with pytest.raises(ValueError):bootstrap.gcs_url(uri)

def test_secret_requires_numeric_pinned_version():
 with pytest.raises(ValueError):bootstrap.secret_value('projects/test/secrets/token/versions/latest')

def test_plugin_upgrade_only_replaces_our_release_link(tmp_path):
 root=tmp_path/'worker';old=root/'releases/old';new=root/'releases/new';relative=Path('self_hosted_worker/custom_nodes/recastfx_worker_nodes')
 for release in [old,new]:
  (release/relative).mkdir(parents=True);(release/relative/'__init__.py').write_text('')
 bootstrap.link_private_plugin(root,old);bootstrap.link_private_plugin(root,new)
 assert (root/'runtime/custom_nodes/recastfx_worker_nodes').resolve()==new/relative

def test_plugin_upgrade_preserves_unfamiliar_link(tmp_path):
 root=tmp_path/'worker';release=root/'releases/new';relative=Path('self_hosted_worker/custom_nodes/recastfx_worker_nodes');(release/relative).mkdir(parents=True);(release/relative/'__init__.py').write_text('')
 custom=root/'runtime/custom_nodes';custom.mkdir(parents=True);foreign=tmp_path/'foreign';foreign.mkdir();plugin=custom/'recastfx_worker_nodes';plugin.symlink_to(foreign)
 with pytest.raises(ValueError):bootstrap.link_private_plugin(root,release)
 assert plugin.is_symlink() and plugin.resolve()==foreign
