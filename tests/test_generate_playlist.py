"""Unit tests for generate_playlist.py"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, mock_open

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import generate_playlist as gp


class TestGetIptvxPlaylists:
    """Tests for get_iptvx_playlists()"""

    def test_returns_empty_when_db_not_exists(self):
        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/nonexistent/path.sqlite')):
            result = gp.get_iptvx_playlists()
            assert result == []

    def test_returns_playlists_with_credentials(self):
        mock_rows = [
            ('xtreme', 'http://server.com/', 'user1', 'pass1'),
            ('other', 'http://other.com/', 'user2', 'pass2'),
        ]

        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/fake/path.sqlite')):
            with patch.object(Path, 'exists', return_value=True):
                with patch('sqlite3.connect') as mock_connect:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchall.return_value = mock_rows
                    mock_conn = MagicMock()
                    mock_conn.cursor.return_value = mock_cursor
                    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                    mock_conn.__exit__ = MagicMock(return_value=False)
                    mock_connect.return_value = mock_conn

                    result = gp.get_iptvx_playlists()

                    assert len(result) == 2
                    assert result[0]['name'] == 'xtreme'
                    assert result[0]['server'] == 'http://server.com'  # trailing slash removed
                    assert result[0]['username'] == 'user1'
                    assert result[0]['password'] == 'pass1'

    def test_filters_out_incomplete_entries(self):
        mock_rows = [
            ('complete', 'http://server.com', 'user', 'pass'),
            ('no_url', None, 'user', 'pass'),
            ('no_pass', 'http://server.com', 'user', None),
        ]

        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/fake/path.sqlite')):
            with patch.object(Path, 'exists', return_value=True):
                with patch('sqlite3.connect') as mock_connect:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchall.return_value = mock_rows
                    mock_conn = MagicMock()
                    mock_conn.cursor.return_value = mock_cursor
                    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                    mock_conn.__exit__ = MagicMock(return_value=False)
                    mock_connect.return_value = mock_conn

                    result = gp.get_iptvx_playlists()

                    assert len(result) == 1
                    assert result[0]['name'] == 'complete'

    def test_handles_database_error(self):
        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/fake/path.sqlite')):
            with patch.object(Path, 'exists', return_value=True):
                with patch('sqlite3.connect', side_effect=sqlite3.Error("DB error")):
                    result = gp.get_iptvx_playlists()
                    assert result == []


class TestSelectPlaylist:
    """Tests for select_playlist()"""

    def test_returns_none_for_empty_list(self):
        result = gp.select_playlist([], None)
        assert result is None

    def test_finds_playlist_by_name(self):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        result = gp.select_playlist(playlists, 'second')
        assert result['name'] == 'second'

    def test_finds_playlist_case_insensitive(self):
        playlists = [{'name': 'MyPlaylist', 'server': 'http://a.com'}]
        result = gp.select_playlist(playlists, 'myplaylist')
        assert result['name'] == 'MyPlaylist'

    def test_auto_selects_single_playlist(self, capsys):
        playlists = [{'name': 'only', 'server': 'http://a.com'}]
        result = gp.select_playlist(playlists, None)
        assert result['name'] == 'only'
        captured = capsys.readouterr()
        assert 'Using IPTVX playlist: only' in captured.out

    def test_exits_when_specified_playlist_not_found(self):
        playlists = [{'name': 'existing', 'server': 'http://a.com'}]
        with pytest.raises(SystemExit):
            gp.select_playlist(playlists, 'nonexistent')

    def test_prompts_for_multiple_playlists(self, monkeypatch):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        monkeypatch.setattr('builtins.input', lambda _: '2')
        result = gp.select_playlist(playlists, None)
        assert result['name'] == 'second'

    def test_handles_quit_input(self, monkeypatch):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        monkeypatch.setattr('builtins.input', lambda _: 'q')
        with pytest.raises(SystemExit):
            gp.select_playlist(playlists, None)


class TestLoadConfig:
    """Tests for load_config()"""

    def test_loads_from_iptvx_database(self):
        mock_playlists = [{'name': 'test', 'server': 'http://s.com', 'username': 'u', 'password': 'p'}]

        with patch.object(gp, 'get_iptvx_playlists', return_value=mock_playlists):
            config = gp.load_config('test')

            assert config['iptv_server'] == 'http://s.com'
            assert config['iptv_username'] == 'u'
            assert config['iptv_password'] == 'p'

    def test_environment_overrides_database(self):
        mock_playlists = [{'name': 'test', 'server': 'http://s.com', 'username': 'u', 'password': 'p'}]

        with patch.object(gp, 'get_iptvx_playlists', return_value=mock_playlists):
            with patch.dict('os.environ', {'IPTV_SERVER': 'http://env.com'}):
                config = gp.load_config('test')
                assert config['iptv_server'] == 'http://env.com'

    def test_exits_when_no_credentials(self):
        with patch.object(gp, 'get_iptvx_playlists', return_value=[]):
            with pytest.raises(SystemExit):
                gp.load_config(None)


class TestRunCommand:
    """Tests for run_command()"""

    def test_returns_stdout_and_returncode(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='output', returncode=0)
            stdout, code = gp.run_command('echo test')
            assert stdout == 'output'
            assert code == 0

    def test_handles_timeout(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('cmd', 1)):
            stdout, code = gp.run_command('sleep 100', timeout=1)
            assert stdout == ''
            assert code == 1


class TestCheckStreamUrl:
    """Tests for check_stream_url()"""

    def test_valid_mp4_stream(self):
        # MP4 magic bytes: 'ftyp' at offset 4
        mp4_header = b'\x00\x00\x00\x20ftypisom' + b'\x00' * 240

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = mp4_header
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert gp.check_stream_url('http://test.com/video.mp4') is True

    def test_valid_mkv_stream(self):
        # MKV/WebM EBML header
        mkv_header = b'\x1a\x45\xdf\xa3' + b'\x00' * 252

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = mkv_header
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert gp.check_stream_url('http://test.com/video.mkv') is True

    def test_html_error_page_returns_false(self):
        html_content = b'<!doctype html><html><body>404 Not Found</body></html>'

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = html_content
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert gp.check_stream_url('http://test.com/video.mp4') is False

    def test_http_404_returns_false(self):
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            'http://test.com', 404, 'Not Found', {}, None
        )):
            assert gp.check_stream_url('http://test.com/video.mp4') is False


class TestParseMoviesFromDescription:
    """Tests for parse_movies_from_description()"""

    def test_extracts_movies_with_timestamps(self):
        description = """
0:00 Introduction
1:30 The Godfather (1972)
15:45 Pulp Fiction (1994)
30:00 Outro
        """
        movies = gp.parse_movies_from_description(description)

        assert len(movies) == 2
        assert movies[0]['name'] == 'The Godfather'
        assert movies[0]['year'] == '1972'
        assert movies[1]['name'] == 'Pulp Fiction'
        assert movies[1]['year'] == '1994'

    def test_handles_movies_without_year(self):
        description = "5:00 Some Movie Title"
        movies = gp.parse_movies_from_description(description)

        assert len(movies) == 1
        assert movies[0]['name'] == 'Some Movie Title'
        assert movies[0]['year'] is None

    def test_skips_intro_outro(self):
        description = """
0:00 Intro
5:00 Real Movie (2000)
60:00 Outro
        """
        movies = gp.parse_movies_from_description(description)
        assert len(movies) == 1
        assert movies[0]['name'] == 'Real Movie'

    def test_handles_empty_description(self):
        movies = gp.parse_movies_from_description('')
        assert movies == []


class TestBuildVodIndex:
    """Tests for build_vod_index()"""

    def test_builds_lowercase_index(self):
        vod_data = [
            {'name': 'The Matrix', 'stream_id': 1},
            {'name': 'INCEPTION', 'stream_id': 2},
        ]
        index = gp.build_vod_index(vod_data)

        assert 'the matrix' in index
        assert 'inception' in index
        assert index['the matrix']['stream_id'] == 1

    def test_skips_entries_without_name(self):
        vod_data = [
            {'name': 'Valid', 'stream_id': 1},
            {'stream_id': 2},  # no name
        ]
        index = gp.build_vod_index(vod_data)
        assert len(index) == 1


class TestFindMovie:
    """Tests for find_movie()"""

    def test_exact_match_with_year(self):
        index = {
            'the matrix 1999': {'name': 'The Matrix 1999', 'stream_id': 1},
            'the matrix 2003': {'name': 'The Matrix 2003', 'stream_id': 2},
        }
        result = gp.find_movie('The Matrix', '1999', index)
        assert result['stream_id'] == 1

    def test_match_by_item_year_field(self):
        index = {
            'the matrix': {'name': 'The Matrix', 'year': '1999', 'stream_id': 1},
        }
        result = gp.find_movie('The Matrix', '1999', index)
        assert result['stream_id'] == 1

    def test_partial_word_match(self):
        index = {
            'the shawshank redemption 1994': {'name': 'The Shawshank Redemption 1994', 'stream_id': 1},
        }
        result = gp.find_movie('Shawshank Redemption', None, index)
        assert result['stream_id'] == 1

    def test_returns_none_when_not_found(self):
        index = {'other movie': {'name': 'Other Movie', 'stream_id': 1}}
        result = gp.find_movie('Nonexistent', '2000', index)
        assert result is None


class TestSanitizeFilename:
    """Tests for sanitize_filename()"""

    def test_removes_at_symbol(self):
        assert gp.sanitize_filename('@Channel') == 'channel'

    def test_replaces_special_chars(self):
        assert gp.sanitize_filename('My Channel!') == 'my-channel'

    def test_handles_multiple_spaces(self):
        assert gp.sanitize_filename('Some  Name') == 'some-name'


class TestFetchChannelVideos:
    """Tests for fetch_channel_videos()"""

    def test_parses_video_list(self):
        mock_output = "abc123|Video Title 1\ndef456|Video Title 2"

        with patch.object(gp, 'run_command', return_value=(mock_output, 0)):
            videos = gp.fetch_channel_videos('@TestChannel')

            assert len(videos) == 2
            assert videos[0]['id'] == 'abc123'
            assert videos[0]['title'] == 'Video Title 1'

    def test_returns_empty_on_error(self):
        with patch.object(gp, 'run_command', return_value=('', 1)):
            videos = gp.fetch_channel_videos('@TestChannel')
            assert videos == []


class TestFetchVideoDescription:
    """Tests for fetch_video_description()"""

    def test_returns_description(self):
        with patch.object(gp, 'run_command', return_value=('Video description', 0)):
            desc = gp.fetch_video_description('abc123')
            assert desc == 'Video description'

    def test_returns_empty_on_error(self):
        with patch.object(gp, 'run_command', return_value=('', 1)):
            desc = gp.fetch_video_description('abc123')
            assert desc == ''


class TestFetchVodCatalog:
    """Tests for fetch_vod_catalog()"""

    def test_fetches_and_parses_catalog(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_data = [{'name': 'Movie', 'stream_id': 1}]

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('builtins.open', mock_open(read_data=json.dumps(vod_data))):
                with patch('os.path.exists', return_value=True):
                    with patch('os.unlink'):
                        result = gp.fetch_vod_catalog(config)
                        assert len(result) == 1
                        assert result[0]['name'] == 'Movie'


class TestParseM3uMetadata:
    """Tests for parse_m3u_metadata()"""

    def test_parses_channel_and_sync_time(self, tmp_path):
        m3u_content = """#EXTM3U
# @playlist_meta
# @channel: @TestChannel
# @synced: 2024-01-01 12:00:00
# @videos_processed: vid1,vid2
"""
        m3u_file = tmp_path / "test.m3u"
        m3u_file.write_text(m3u_content)

        metadata = gp.parse_m3u_metadata(m3u_file)

        assert metadata['channel'] == '@TestChannel'
        assert metadata['synced'] == '2024-01-01 12:00:00'
        assert 'vid1' in metadata['videos_processed']
        assert 'vid2' in metadata['videos_processed']

    def test_returns_none_for_nonexistent_file(self, tmp_path):
        result = gp.parse_m3u_metadata(tmp_path / "nonexistent.m3u")
        assert result is None


class TestGenerateM3uContent:
    """Tests for generate_m3u_content()"""

    def test_generates_valid_m3u(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'test movie': {'name': 'Test Movie', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        video_data = [{
            'video_id': 'vid1',
            'title': 'Video Title',
            'movies': [{'name': 'Test Movie', 'year': '2020'}]
        }]

        content, stats = gp.generate_m3u_content('@Channel', video_data, config, vod_index)

        assert '#EXTM3U' in content
        assert '# @channel: @Channel' in content
        assert stats['matched'] == 1
        assert 'http://s.com/movie/u/p/123.mp4' in content

    def test_tracks_unmatched_movies(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {}  # empty - nothing will match
        video_data = [{
            'video_id': 'vid1',
            'title': 'Video Title',
            'movies': [{'name': 'Unknown Movie', 'year': '2020'}]
        }]

        content, stats = gp.generate_m3u_content('@Channel', video_data, config, vod_index)

        assert stats['unmatched'] == 1
        assert stats['matched'] == 0


class TestListIptvxPlaylists:
    """Tests for list_iptvx_playlists()"""

    def test_returns_none_when_no_playlists(self, capsys):
        with patch.object(gp, 'get_iptvx_playlists', return_value=[]):
            result = gp.list_iptvx_playlists()
            assert result is None
            captured = capsys.readouterr()
            assert 'No IPTVX playlists' in captured.out

    def test_auto_selects_single_playlist(self, capsys):
        playlists = [{'name': 'only', 'server': 'http://a.com'}]
        with patch.object(gp, 'get_iptvx_playlists', return_value=playlists):
            result = gp.list_iptvx_playlists()
            assert result['name'] == 'only'


class TestMain:
    """Tests for main() CLI handling"""

    def test_shows_usage_when_no_channel(self, capsys):
        with patch('sys.argv', ['generate_playlist.py']):
            with patch.object(gp, 'get_iptvx_playlists', return_value=[
                {'name': 'test', 'server': 'http://s.com', 'username': 'u', 'password': 'p'}
            ]):
                with pytest.raises(SystemExit) as exc_info:
                    gp.main()
                assert exc_info.value.code == 1
                captured = capsys.readouterr()
                assert 'No channel specified' in captured.out

    def test_playlists_flag(self, capsys):
        with patch('sys.argv', ['generate_playlist.py', '--playlists']):
            with patch.object(gp, 'get_iptvx_playlists', return_value=[
                {'name': 'test', 'server': 'http://s.com', 'username': 'u', 'password': 'p'}
            ]):
                gp.main()
                captured = capsys.readouterr()
                assert 'test' in captured.out

    def test_processes_channel_argument(self, capsys):
        with patch('sys.argv', ['generate_playlist.py', '@TestChannel']):
            with patch.object(gp, 'load_config', return_value={
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }):
                with patch.object(gp, 'fetch_vod_catalog', return_value=[]):
                    with pytest.raises(SystemExit):
                        gp.main()


class TestProcessChannel:
    """Tests for process_channel()"""

    def test_returns_video_data(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {}

        with patch.object(gp, 'fetch_channel_videos', return_value=[
            {'id': 'vid1', 'title': 'Test Video'}
        ]):
            with patch.object(gp, 'fetch_video_description', return_value='5:00 Movie (2020)'):
                result = gp.process_channel('@Test', config, vod_index)

                assert len(result) == 1
                assert result[0]['video_id'] == 'vid1'
                assert len(result[0]['movies']) == 1

    def test_returns_empty_when_no_videos(self):
        config = {}
        vod_index = {}

        with patch.object(gp, 'fetch_channel_videos', return_value=[]):
            result = gp.process_channel('@Test', config, vod_index)
            assert result == []


class TestSyncPlaylists:
    """Tests for sync_playlists()"""

    def test_skips_when_no_playlist_dir(self, capsys, tmp_path):
        config = {'output_dir': 'nonexistent'}
        with patch.object(gp, 'SCRIPT_DIR', tmp_path):
            gp.sync_playlists(config)
            captured = capsys.readouterr()
            assert 'No playlists directory found' in captured.out

    def test_skips_when_vod_fetch_fails(self, capsys, tmp_path):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()
        config = {'output_dir': 'playlists', 'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}

        with patch.object(gp, 'SCRIPT_DIR', tmp_path):
            with patch.object(gp, 'fetch_vod_catalog', return_value=[]):
                gp.sync_playlists(config)
                captured = capsys.readouterr()
                assert 'Failed to fetch VOD catalog' in captured.out

    def test_skips_files_without_metadata(self, capsys, tmp_path):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()
        (playlist_dir / 'test.m3u').write_text('#EXTM3U\n')
        config = {'output_dir': 'playlists', 'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}

        with patch.object(gp, 'SCRIPT_DIR', tmp_path):
            with patch.object(gp, 'fetch_vod_catalog', return_value=[{'name': 'Movie', 'stream_id': 1}]):
                gp.sync_playlists(config)
                captured = capsys.readouterr()
                assert 'Skipping' in captured.out

    def test_syncs_playlist_with_metadata(self, capsys, tmp_path):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()
        m3u_content = """#EXTM3U
# @playlist_meta
# @channel: @TestChannel
# @synced: 2024-01-01 12:00:00
# @videos_processed: vid1
"""
        (playlist_dir / 'test.m3u').write_text(m3u_content)
        config = {'output_dir': 'playlists', 'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}

        with patch.object(gp, 'SCRIPT_DIR', tmp_path):
            with patch.object(gp, 'fetch_vod_catalog', return_value=[{'name': 'Movie', 'stream_id': 1}]):
                with patch.object(gp, 'process_channel', return_value=[]):
                    with patch.object(gp, 'generate_m3u_content', return_value=('#EXTM3U\n', {'matched': 0, 'unmatched': 0, 'new_matched': 0, 'restored': 0, 'broken': 0, 'unavailable': 0})):
                        gp.sync_playlists(config)
                        captured = capsys.readouterr()
                        assert 'Syncing test.m3u' in captured.out


class TestCheckStreamUrlAdditional:
    """Additional tests for check_stream_url()"""

    def test_valid_avi_stream(self):
        avi_header = b'RIFF' + b'\x00' * 4 + b'AVI ' + b'\x00' * 244
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = avi_header
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            assert gp.check_stream_url('http://test.com/video.avi') is True

    def test_valid_mpeg_ts_stream(self):
        ts_header = b'\x47' + b'\x00' * 255
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = ts_header
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            assert gp.check_stream_url('http://test.com/video.ts') is True

    def test_valid_flv_stream(self):
        flv_header = b'FLV' + b'\x00' * 253
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = flv_header
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            assert gp.check_stream_url('http://test.com/video.flv') is True

    def test_valid_mpeg_ps_stream(self):
        ps_header = b'\x00\x00\x01\xba' + b'\x00' * 252
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = ps_header
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            assert gp.check_stream_url('http://test.com/video.vob') is True

    def test_short_data_with_retries(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b'\x00' * 4  # too short
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            assert gp.check_stream_url('http://test.com/video.mp4', retries=0) is False

    def test_generic_binary_data_passes(self):
        # Binary data that's not HTML and >= 64 bytes
        binary_data = b'\x00\x01\x02\x03' * 64
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = binary_data
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            assert gp.check_stream_url('http://test.com/video.mp4') is True

    def test_http_502_returns_false(self):
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            'http://test.com', 502, 'Bad Gateway', {}, None
        )):
            assert gp.check_stream_url('http://test.com/video.mp4') is False

    def test_network_error_with_retries(self):
        with patch('urllib.request.urlopen', side_effect=Exception("Network error")):
            assert gp.check_stream_url('http://test.com/video.mp4', retries=0) is False


class TestSelectPlaylistAdditional:
    """Additional tests for select_playlist()"""

    def test_handles_invalid_input_then_valid(self, monkeypatch):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        inputs = iter(['invalid', '99', '1'])
        monkeypatch.setattr('builtins.input', lambda _: next(inputs))
        result = gp.select_playlist(playlists, None)
        assert result['name'] == 'first'

    def test_handles_eof(self, monkeypatch):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        monkeypatch.setattr('builtins.input', MagicMock(side_effect=EOFError))
        with pytest.raises(SystemExit):
            gp.select_playlist(playlists, None)


class TestListIptvxPlaylistsAdditional:
    """Additional tests for list_iptvx_playlists()"""

    def test_prompts_for_multiple_playlists(self, monkeypatch, capsys):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        monkeypatch.setattr('builtins.input', lambda _: '2')
        with patch.object(gp, 'get_iptvx_playlists', return_value=playlists):
            result = gp.list_iptvx_playlists()
            assert result['name'] == 'second'

    def test_handles_quit(self, monkeypatch, capsys):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        monkeypatch.setattr('builtins.input', lambda _: 'q')
        with patch.object(gp, 'get_iptvx_playlists', return_value=playlists):
            result = gp.list_iptvx_playlists()
            assert result is None

    def test_handles_invalid_then_valid(self, monkeypatch, capsys):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        inputs = iter(['bad', '1'])
        monkeypatch.setattr('builtins.input', lambda _: next(inputs))
        with patch.object(gp, 'get_iptvx_playlists', return_value=playlists):
            result = gp.list_iptvx_playlists()
            assert result['name'] == 'first'

    def test_handles_eof(self, monkeypatch, capsys):
        playlists = [
            {'name': 'first', 'server': 'http://a.com'},
            {'name': 'second', 'server': 'http://b.com'},
        ]
        monkeypatch.setattr('builtins.input', MagicMock(side_effect=EOFError))
        with patch.object(gp, 'get_iptvx_playlists', return_value=playlists):
            result = gp.list_iptvx_playlists()
            assert result is None


class TestParseM3uMetadataAdditional:
    """Additional tests for parse_m3u_metadata()"""

    def test_parses_entry_blocks(self, tmp_path):
        m3u_content = """#EXTM3U
# @playlist_meta
# @channel: @TestChannel
# @synced: 2024-01-01 12:00:00
# @videos_processed: vid1
# @entry_start
# @video: vid1 | Video Title
# @movie: Test Movie | year: 2020 | matched
# stream_id: 123
#EXTINF:-1 tvg-name="Test Movie",Test Movie
http://server.com/movie/u/p/123.mp4
# @entry_end
"""
        m3u_file = tmp_path / "test.m3u"
        m3u_file.write_text(m3u_content)

        metadata = gp.parse_m3u_metadata(m3u_file)

        assert len(metadata['entries']) == 1
        entry = metadata['entries'][0]
        assert entry['state'] == 'matched'
        assert entry['stream_id'] == '123'
        assert entry['video']['id'] == 'vid1'
        assert entry['movie']['name'] == 'Test Movie'


class TestGenerateM3uContentAdditional:
    """Additional tests for generate_m3u_content()"""

    def test_handles_existing_metadata(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'test movie': {'name': 'Test Movie', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        video_data = []
        existing_metadata = {
            'channel': '@Channel',
            'synced': '2024-01-01',
            'videos_processed': {'vid1'},
            'entries': [{
                'movie': {'name': 'Test Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'unmatched',
                'stream_id': None,
                'lines': []
            }]
        }

        content, stats = gp.generate_m3u_content('@Channel', video_data, config, vod_index, existing_metadata)

        assert stats['new_matched'] == 1  # was unmatched, now matched

    def test_marks_unavailable_when_match_disappears(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {}  # Empty - previously matched movie no longer available
        video_data = []
        existing_metadata = {
            'channel': '@Channel',
            'synced': '2024-01-01',
            'videos_processed': {'vid1'},
            'entries': [{
                'movie': {'name': 'Missing Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'matched',
                'stream_id': '123',
                'lines': []
            }]
        }

        content, stats = gp.generate_m3u_content('@Channel', video_data, config, vod_index, existing_metadata)

        assert stats['unavailable'] == 1

    def test_restores_previously_unavailable(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'restored movie': {'name': 'Restored Movie', 'stream_id': 456, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        video_data = []
        existing_metadata = {
            'channel': '@Channel',
            'synced': '2024-01-01',
            'videos_processed': {'vid1'},
            'entries': [{
                'movie': {'name': 'Restored Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'unavailable',
                'stream_id': '123',
                'lines': []
            }]
        }

        content, stats = gp.generate_m3u_content('@Channel', video_data, config, vod_index, existing_metadata)

        assert stats['restored'] == 1


class TestMainAdditional:
    """Additional tests for main()"""

    def test_sync_mode(self, tmp_path, capsys):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()

        with patch('sys.argv', ['generate_playlist.py', '--sync']):
            with patch.object(gp, 'load_config', return_value={
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }):
                with patch.object(gp, 'SCRIPT_DIR', tmp_path):
                    with patch.object(gp, 'fetch_vod_catalog', return_value=[]):
                        gp.main()
                        captured = capsys.readouterr()
                        assert 'Failed to fetch VOD catalog' in captured.out

    def test_full_channel_processing(self, tmp_path, capsys):
        playlist_dir = tmp_path / 'playlists'

        with patch('sys.argv', ['generate_playlist.py', '@TestChannel']):
            with patch.object(gp, 'load_config', return_value={
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }):
                with patch.object(gp, 'SCRIPT_DIR', tmp_path):
                    with patch.object(gp, 'fetch_vod_catalog', return_value=[{'name': 'Movie', 'stream_id': 1}]):
                        with patch.object(gp, 'process_channel', return_value=[{
                            'video_id': 'v1',
                            'title': 'Test',
                            'movies': [{'name': 'Movie', 'year': '2020'}]
                        }]):
                            gp.main()
                            # Check playlist was created
                            assert (playlist_dir / 'testchannel.m3u').exists()


class TestFetchVodCatalogAdditional:
    """Additional tests for fetch_vod_catalog()"""

    def test_handles_curl_failure(self, capsys):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = gp.fetch_vod_catalog(config)
            assert result == []

    def test_handles_json_decode_error(self, capsys):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('builtins.open', mock_open(read_data='not json')):
                with patch('os.path.exists', return_value=True):
                    with patch('os.unlink'):
                        result = gp.fetch_vod_catalog(config)
                        assert result == []


class TestProcessChannelAdditional:
    """Additional tests for process_channel()"""

    def test_filters_new_videos_in_sync_mode(self, capsys):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {}
        existing_metadata = {
            'videos_processed': {'old_vid'},
            'entries': []
        }

        with patch.object(gp, 'fetch_channel_videos', return_value=[
            {'id': 'old_vid', 'title': 'Old Video'},
            {'id': 'new_vid', 'title': 'New Video'}
        ]):
            with patch.object(gp, 'fetch_video_description', return_value='5:00 Movie (2020)'):
                result = gp.process_channel('@Test', config, vod_index, existing_metadata, new_videos_only=True)
                # Should only process new_vid
                assert len(result) == 1
                assert result[0]['video_id'] == 'new_vid'

    def test_returns_empty_when_no_new_videos(self, capsys):
        config = {}
        vod_index = {}
        existing_metadata = {
            'videos_processed': {'vid1'},
            'entries': []
        }

        with patch.object(gp, 'fetch_channel_videos', return_value=[
            {'id': 'vid1', 'title': 'Old Video'}
        ]):
            result = gp.process_channel('@Test', config, vod_index, existing_metadata, new_videos_only=True)
            assert result == []
            captured = capsys.readouterr()
            assert 'No new videos' in captured.out
