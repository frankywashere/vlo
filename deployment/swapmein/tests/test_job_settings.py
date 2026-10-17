from swapmein.job_settings import settings_used


def test_settings_use_job_snapshot_not_current_defaults_and_exclude_private_data():
    job = {'setup': {'trim': [1.3, 6.3], 'easy_mode': False, 'enhanced_character_masking': True,
                    'replace_all_people': False, 'targets': [{'reference_index': 0}, {'reference_index': 1}]},
           'audio_processing': False, 'mask_style': 'depth_multi', 'duration': 5, 'width': 1364, 'height': 768,
           'preparation_media': {'width': 1280, 'height': 720, 'fps': 24},
           'generation_settings': {'model': 'minimax-h3', 'resolution': '768P'},
           'provider_key': 'private', 'source_uri': 'gs://private/source',
           'settings_used': {'provider_key': 'also-private'}}
    result = settings_used(job)
    assert result['trim'] == [1.3, 6.3]
    assert result['easy_mode'] is False and result['enhanced_masking'] is True
    assert result['audio_processing'] is False and result['people_count'] == 2 and result['reference_count'] == 2
    assert result['model'] == 'MiniMax H3' and result['requested_resolution'] == '768p'
    assert result['prep_width'] == 1280 and result['width'] == 1364
    assert 'private' not in str(result)


def test_missing_legacy_settings_are_not_invented_and_saved_settings_survive():
    assert settings_used({'status': 'completed', 'width': 854}) == {'width': 854}
    assert settings_used({'settings_used': {'easy_mode': False, 'audio_processing': True}}) == {'easy_mode': False, 'audio_processing': True}
