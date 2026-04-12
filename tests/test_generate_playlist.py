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
    """Tests for load_config() — thin wrapper around load_configs()"""

    def test_loads_from_iptvx_database(self):
        mock_playlists = [{'name': 'test', 'server': 'http://s.com', 'username': 'u', 'password': 'p'}]

        with patch.object(gp, 'get_iptvx_playlists', return_value=mock_playlists):
            config = gp.load_config('test')

            assert config['iptv_server'] == 'http://s.com'
            assert config['iptv_username'] == 'u'
            assert config['iptv_password'] == 'p'

    def test_environment_overrides_database(self):
        with patch.dict('os.environ', {
            'IPTV_SERVER': 'http://env.com',
            'IPTV_USERNAME': 'env_u',
            'IPTV_PASSWORD': 'env_p',
        }):
            config = gp.load_config(None)
            assert config['iptv_server'] == 'http://env.com'
            assert config['iptv_username'] == 'env_u'

    def test_exits_when_no_credentials(self):
        with patch.object(gp, 'get_iptvx_playlists', return_value=[]):
            with pytest.raises(SystemExit):
                gp.load_config(None)


class TestLoadConfigs:
    """Tests for load_configs()"""

    def test_returns_all_playlists_when_no_name(self):
        mock_playlists = [
            {'name': 'xtreme', 'server': 'http://a.com', 'username': 'u1', 'password': 'p1'},
            {'name': 'new', 'server': 'http://b.com', 'username': 'u2', 'password': 'p2'},
        ]
        with patch.object(gp, 'get_iptvx_playlists', return_value=mock_playlists):
            configs = gp.load_configs(None)
            assert len(configs) == 2
            assert configs[0]['iptv_server'] == 'http://a.com'
            assert configs[0]['_name'] == 'xtreme'
            assert configs[1]['iptv_server'] == 'http://b.com'

    def test_returns_single_when_name_specified(self):
        mock_playlists = [
            {'name': 'xtreme', 'server': 'http://a.com', 'username': 'u1', 'password': 'p1'},
            {'name': 'new', 'server': 'http://b.com', 'username': 'u2', 'password': 'p2'},
        ]
        with patch.object(gp, 'get_iptvx_playlists', return_value=mock_playlists):
            configs = gp.load_configs('xtreme')
            assert len(configs) == 1
            assert configs[0]['iptv_server'] == 'http://a.com'

    def test_env_vars_override_everything(self):
        with patch.dict('os.environ', {
            'IPTV_SERVER': 'http://env.com',
            'IPTV_USERNAME': 'env_u',
            'IPTV_PASSWORD': 'env_p',
        }):
            configs = gp.load_configs(None)
            assert len(configs) == 1
            assert configs[0]['iptv_server'] == 'http://env.com'

    def test_exits_when_no_credentials(self):
        with patch.object(gp, 'get_iptvx_playlists', return_value=[]):
            with pytest.raises(SystemExit):
                gp.load_configs(None)


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

    def test_first_wins_dedup(self):
        """First item with same lowercase name wins (priority order)."""
        vod_data = [
            {'name': 'The Matrix', 'stream_id': 1, '_source': {'server': 'http://a.com'}},
            {'name': 'The Matrix', 'stream_id': 2, '_source': {'server': 'http://b.com'}},
        ]
        index = gp.build_vod_index(vod_data)
        assert index['the matrix']['stream_id'] == 1


class TestBuildStreamUrl:
    """Tests for build_stream_url()"""

    def test_uses_embedded_source(self):
        result = {
            'stream_id': 123,
            'container_extension': 'mkv',
            '_source': {'server': 'http://a.com', 'username': 'u1', 'password': 'p1'},
        }
        url = gp.build_stream_url(result)
        assert url == 'http://a.com/movie/u1/p1/123.mkv'

    def test_falls_back_to_config(self):
        result = {'stream_id': 456, 'container_extension': 'mp4'}
        config = {'iptv_server': 'http://b.com', 'iptv_username': 'u2', 'iptv_password': 'p2'}
        url = gp.build_stream_url(result, config)
        assert url == 'http://b.com/movie/u2/p2/456.mp4'

    def test_defaults_to_mp4_extension(self):
        result = {'stream_id': 789, '_source': {'server': 'http://c.com', 'username': 'u', 'password': 'p'}}
        url = gp.build_stream_url(result)
        assert url.endswith('/789.mp4')


class TestFetchMultiVodCatalog:
    """Tests for fetch_multi_vod_catalog()"""

    def test_merges_catalogs_with_source(self):
        configs = [
            {'iptv_server': 'http://a.com', 'iptv_username': 'u1', 'iptv_password': 'p1', '_name': 'first'},
            {'iptv_server': 'http://b.com', 'iptv_username': 'u2', 'iptv_password': 'p2', '_name': 'second'},
        ]
        with patch.object(gp, 'fetch_vod_catalog', side_effect=[
            [{'name': 'Movie A', 'stream_id': 1}],
            [{'name': 'Movie B', 'stream_id': 2}],
        ]):
            merged = gp.fetch_multi_vod_catalog(configs)
            assert len(merged) == 2
            assert merged[0]['_source']['name'] == 'first'
            assert merged[0]['_source']['server'] == 'http://a.com'
            assert merged[1]['_source']['name'] == 'second'

    def test_returns_empty_when_all_fail(self):
        configs = [{'iptv_server': 'http://a.com', 'iptv_username': 'u', 'iptv_password': 'p', '_name': 'x'}]
        with patch.object(gp, 'fetch_vod_catalog', return_value=[]):
            merged = gp.fetch_multi_vod_catalog(configs)
            assert merged == []


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


class TestGenerateM3u:
    """Tests for generate_m3u() unified pipeline"""

    def test_generates_valid_m3u_from_video_data(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'test movie': {'name': 'Test Movie', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        video_data = [{
            'video_id': 'vid1',
            'title': 'Video Title',
            'movies': [{'name': 'Test Movie', 'year': '2020'}]
        }]
        movies = gp.movies_from_video_data(video_data)
        header = [f"{gp.META_CHANNEL} @Channel"]

        content, stats = gp.generate_m3u('@Channel', movies, config, vod_index, header_lines=header)

        assert '#EXTM3U' in content
        assert '# @channel: @Channel' in content
        assert stats['matched'] == 1
        assert 'http://s.com/movie/u/p/123.mp4' in content

    def test_includes_source_metadata_when_present(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'test movie': {
                'name': 'Test Movie', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': '',
                '_source': {'name': 'xtreme', 'server': 'http://other.com', 'username': 'u2', 'password': 'p2'},
            }
        }
        video_data = [{
            'video_id': 'vid1',
            'title': 'Video Title',
            'movies': [{'name': 'Test Movie', 'year': '2020'}]
        }]
        movies = gp.movies_from_video_data(video_data)

        content, stats = gp.generate_m3u('@Channel', movies, config, vod_index)

        assert '# @source: xtreme' in content
        # URL should use _source credentials, not config
        assert 'http://other.com/movie/u2/p2/123.mp4' in content

    def test_tracks_unmatched_movies(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {}  # empty - nothing will match
        video_data = [{
            'video_id': 'vid1',
            'title': 'Video Title',
            'movies': [{'name': 'Unknown Movie', 'year': '2020'}]
        }]
        movies = gp.movies_from_video_data(video_data)

        content, stats = gp.generate_m3u('@Channel', movies, config, vod_index)

        assert stats['unmatched'] == 1
        assert stats['matched'] == 0

    def test_generates_valid_m3u_from_file_data(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'who by fire': {'name': 'Who by Fire', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        parsed = [{"title": "Comme le feu", "title_en": "Who by Fire", "year": "2024"}]
        movies = gp.movies_from_file(parsed, "Quebec-Movies")
        header = ["# @name: Quebec-Movies", "# @source_type: file"]

        content, stats = gp.generate_m3u("Quebec-Movies", movies, config, vod_index, header_lines=header)

        assert '#EXTM3U' in content
        assert '# @name: Quebec-Movies' in content
        assert '# @source_type: file' in content
        assert stats['matched'] == 1
        assert 'stream_id: 123' in content

    def test_same_pipeline_for_all_sources(self):
        """Both YouTube and file sources go through the same matching and rendering."""
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'test movie': {'name': 'Test Movie', 'stream_id': 99, 'container_extension': 'mp4', 'stream_icon': ''}
        }

        # From YouTube
        yt_movies = gp.movies_from_video_data([{
            'video_id': 'v1', 'title': 'Vid',
            'movies': [{'name': 'Test Movie', 'year': '2020'}]
        }])
        yt_content, yt_stats = gp.generate_m3u('YT', yt_movies, config, vod_index)

        # From file
        file_movies = gp.movies_from_file(
            [{"title": "Test Movie", "title_en": "", "year": "2020"}], "File"
        )
        file_content, file_stats = gp.generate_m3u('File', file_movies, config, vod_index)

        # Same match results
        assert yt_stats['matched'] == file_stats['matched'] == 1
        # Both contain the stream URL
        assert 'http://s.com/movie/u/p/99.mp4' in yt_content
        assert 'http://s.com/movie/u/p/99.mp4' in file_content


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
                assert 'usage:' in captured.out  # Shows full help

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
            with patch.object(gp, 'load_configs', return_value=[{
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }]):
                with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[]):
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
            with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[]):
                gp.sync_playlists(config)
                captured = capsys.readouterr()
                assert 'Failed to fetch VOD catalog' in captured.out

    def test_skips_files_without_metadata(self, capsys, tmp_path):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()
        (playlist_dir / 'test.m3u').write_text('#EXTM3U\n')
        config = {'output_dir': 'playlists', 'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}

        with patch.object(gp, 'SCRIPT_DIR', tmp_path):
            with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[{'name': 'Movie', 'stream_id': 1}]):
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
            with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[{'name': 'Movie', 'stream_id': 1}]):
                with patch.object(gp, 'process_channel', return_value=[]):
                    with patch.object(gp, 'generate_m3u', return_value=('#EXTM3U\n', {'matched': 0, 'unmatched': 0, 'new_matched': 0, 'restored': 0, 'broken': 0, 'unavailable': 0})):
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

    def test_parses_source_tag(self, tmp_path):
        m3u_content = """#EXTM3U
# @playlist_meta
# @channel: @TestChannel
# @synced: 2024-01-01 12:00:00
# @videos_processed: vid1
# @entry_start
# @video: vid1 | Video Title
# @movie: Test Movie | year: 2020 | matched
# @source: xtreme
# stream_id: 123
#EXTINF:-1 tvg-name="Test Movie",Test Movie
http://server.com/movie/u/p/123.mp4
# @entry_end
"""
        m3u_file = tmp_path / "test.m3u"
        m3u_file.write_text(m3u_content)

        metadata = gp.parse_m3u_metadata(m3u_file)
        entry = metadata['entries'][0]
        assert entry['source'] == 'xtreme'


class TestPreprocessExistingEntries:
    """Tests for preprocess_existing_entries() and sync state transitions"""

    def test_new_matched_from_unmatched(self):
        vod_index = {
            'test movie': {'name': 'Test Movie', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        metadata = {
            'entries': [{
                'movie': {'name': 'Test Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'unmatched',
                'stream_id': None,
                'lines': []
            }]
        }

        entries = gp.preprocess_existing_entries(metadata, vod_index)
        key = 'Test Movie|2020'
        assert entries[key]['state'] == 'new_matched'

    def test_marks_unavailable_when_match_disappears(self):
        vod_index = {}  # Empty - previously matched movie no longer available
        metadata = {
            'entries': [{
                'movie': {'name': 'Missing Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'matched',
                'stream_id': '123',
                'lines': []
            }]
        }

        entries = gp.preprocess_existing_entries(metadata, vod_index)
        key = 'Missing Movie|2020'
        assert entries[key]['state'] == 'unavailable'

    def test_restores_previously_unavailable(self):
        vod_index = {
            'restored movie': {'name': 'Restored Movie', 'stream_id': 456, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        metadata = {
            'entries': [{
                'movie': {'name': 'Restored Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'unavailable',
                'stream_id': '123',
                'lines': []
            }]
        }

        entries = gp.preprocess_existing_entries(metadata, vod_index)
        key = 'Restored Movie|2020'
        assert entries[key]['state'] == 'restored'

    def test_state_transitions_through_full_pipeline(self):
        """Verify preprocess + generate_m3u handles state transitions end-to-end."""
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        vod_index = {
            'test movie': {'name': 'Test Movie', 'stream_id': 123, 'container_extension': 'mp4', 'stream_icon': ''}
        }
        metadata = {
            'entries': [{
                'movie': {'name': 'Test Movie', 'year': '2020'},
                'video': {'id': 'vid1', 'title': 'Test'},
                'state': 'unmatched',
                'stream_id': None,
                'lines': []
            }]
        }

        existing = gp.preprocess_existing_entries(metadata, vod_index)
        content, stats = gp.generate_m3u('@Ch', [], config, vod_index, existing_entries=existing)

        assert stats['new_matched'] == 1
        assert 'stream_id: 123' in content


class TestMainAdditional:
    """Additional tests for main()"""

    def test_sync_mode(self, tmp_path, capsys):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()

        with patch('sys.argv', ['generate_playlist.py', '--sync']):
            with patch.object(gp, 'load_configs', return_value=[{
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }]):
                with patch.object(gp, 'SCRIPT_DIR', tmp_path):
                    with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[]):
                        gp.main()
                        captured = capsys.readouterr()
                        assert 'Failed to fetch VOD catalog' in captured.out

    def test_full_channel_processing(self, tmp_path, capsys):
        playlist_dir = tmp_path / 'playlists'

        with patch('sys.argv', ['generate_playlist.py', '@TestChannel']):
            with patch.object(gp, 'load_configs', return_value=[{
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }]):
                with patch.object(gp, 'SCRIPT_DIR', tmp_path):
                    with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[{'name': 'Movie', 'stream_id': 1}]):
                        with patch.object(gp, 'process_channel', return_value=[{
                            'video_id': 'v1',
                            'title': 'Test',
                            'movies': [{'name': 'Movie', 'year': '2020'}]
                        }]):
                            gp.main()
                            # Check playlist was created
                            assert (playlist_dir / 'testchannel.m3u').exists()

    def test_from_file_series_mode(self, tmp_path, capsys):
        csv_file = tmp_path / 'shows.csv'
        csv_file.write_text("title,start_year,genre\nTest Show,2020,Drame\n")
        playlist_dir = tmp_path / 'playlists'

        with patch('sys.argv', ['generate_playlist.py', '--from-file', str(csv_file),
                                '--name', 'Test', '--type', 'series']):
            with patch.object(gp, 'load_configs', return_value=[{
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }]):
                with patch.object(gp, 'SCRIPT_DIR', tmp_path):
                    with patch.object(gp, 'fetch_multi_series_catalog', return_value=[
                        {'name': 'Test Show (2020)', 'title': 'Test Show', 'series_id': 1,
                         'year': '2020', 'cover': '', '_source': {
                             'name': 'p1', 'server': 'http://s.com', 'username': 'u', 'password': 'p'
                         }}
                    ]):
                        with patch.object(gp, 'fetch_series_episodes', return_value=[
                            {'id': 10, 'title': 'Pilot', 'season': 1, 'episode_num': 1, 'container_extension': 'mp4'}
                        ]):
                            gp.main()
                            m3u = (playlist_dir / 'test.m3u')
                            assert m3u.exists()
                            content = m3u.read_text()
                            assert '/series/' in content
                            assert 'S01E01' in content

    def test_from_file_append_mode(self, tmp_path, capsys):
        playlist_dir = tmp_path / 'playlists'
        playlist_dir.mkdir()
        existing_m3u = playlist_dir / 'test.m3u'
        existing_m3u.write_text('#EXTM3U\n# existing content\n')

        csv_file = tmp_path / 'movies.csv'
        csv_file.write_text("title,year\nTest Movie,2020\n")

        with patch('sys.argv', ['generate_playlist.py', '--from-file', str(csv_file),
                                '--name', 'Test', '--append']):
            with patch.object(gp, 'load_configs', return_value=[{
                'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p', 'output_dir': 'playlists'
            }]):
                with patch.object(gp, 'SCRIPT_DIR', tmp_path):
                    with patch.object(gp, 'fetch_multi_vod_catalog', return_value=[
                        {'name': 'Test Movie', 'stream_id': 1, 'container_extension': 'mp4', 'stream_icon': ''}
                    ]):
                        with patch.object(gp, 'check_stream_url', return_value=True):
                            gp.main()
                            content = existing_m3u.read_text()
                            assert '# existing content' in content
                            assert 'Test Movie' in content


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


class TestCheckRclone:
    """Tests for _check_rclone()"""

    def test_rclone_not_installed(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            ok, err = gp._check_rclone()
            assert ok is False
            assert 'not installed' in err

    def test_rclone_remote_not_configured(self):
        with patch('subprocess.run') as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd == ['which', 'rclone']:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0, stdout='other_remote:\n')
            mock_run.side_effect = side_effect
            ok, err = gp._check_rclone()
            assert ok is False
            assert 'not configured' in err

    def test_rclone_configured(self):
        with patch('subprocess.run') as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd == ['which', 'rclone']:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0, stdout='gdrive:\n')
            mock_run.side_effect = side_effect
            ok, err = gp._check_rclone()
            assert ok is True
            assert err is None


class TestAddPlaylistToIptvx:
    """Tests for add_playlist_to_iptvx()"""

    def test_returns_false_when_db_missing(self):
        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/nonexistent')):
            assert gp.add_playlist_to_iptvx('test', 'http://url') is False

    def test_updates_existing_playlist(self):
        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/fake/path')):
            with patch.object(Path, 'exists', return_value=True):
                with patch('sqlite3.connect') as mock_connect:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = (1,)  # existing
                    mock_conn = MagicMock()
                    mock_conn.cursor.return_value = mock_cursor
                    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                    mock_conn.__exit__ = MagicMock(return_value=False)
                    mock_connect.return_value = mock_conn

                    assert gp.add_playlist_to_iptvx('test', 'http://url') is True

    def test_returns_false_when_playlist_not_found(self):
        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/fake/path')):
            with patch.object(Path, 'exists', return_value=True):
                with patch('sqlite3.connect') as mock_connect:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = None  # not found
                    mock_conn = MagicMock()
                    mock_conn.cursor.return_value = mock_cursor
                    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                    mock_conn.__exit__ = MagicMock(return_value=False)
                    mock_connect.return_value = mock_conn

                    assert gp.add_playlist_to_iptvx('test', 'http://url') is False

    def test_handles_sqlite_error(self):
        with patch.object(gp, 'IPTVX_SQLITE_PATH', Path('/fake/path')):
            with patch.object(Path, 'exists', return_value=True):
                with patch('sqlite3.connect', side_effect=sqlite3.Error("fail")):
                    assert gp.add_playlist_to_iptvx('test', 'http://url') is False


class TestUploadToGdrive:
    """Tests for upload_to_gdrive()"""

    def test_returns_none_when_rclone_not_configured(self, tmp_path):
        with patch.object(gp, '_check_rclone', return_value=(False, 'not installed')):
            assert gp.upload_to_gdrive(tmp_path / 'test.m3u') is None

    def test_returns_none_on_upload_failure(self, tmp_path):
        test_file = tmp_path / 'test.m3u'
        test_file.write_text('#EXTM3U\n')
        with patch.object(gp, '_check_rclone', return_value=(True, None)):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr='error')
                assert gp.upload_to_gdrive(test_file) is None

    def test_successful_upload(self, tmp_path):
        test_file = tmp_path / 'test.m3u'
        test_file.write_text('#EXTM3U\n')
        with patch.object(gp, '_check_rclone', return_value=(True, None)):
            with patch('subprocess.run') as mock_run:
                def side_effect(cmd, **kwargs):
                    if 'copy' in cmd:
                        return MagicMock(returncode=0)
                    if 'lsjson' in cmd:
                        return MagicMock(returncode=0, stdout=json.dumps([{'ID': 'file123'}]))
                    return MagicMock(returncode=0)
                mock_run.side_effect = side_effect
                result = gp.upload_to_gdrive(test_file)
                assert result is not None
                assert result['file_id'] == 'file123'

    def test_returns_none_on_lsjson_failure(self, tmp_path):
        test_file = tmp_path / 'test.m3u'
        test_file.write_text('#EXTM3U\n')
        with patch.object(gp, '_check_rclone', return_value=(True, None)):
            with patch('subprocess.run') as mock_run:
                def side_effect(cmd, **kwargs):
                    if 'copy' in cmd:
                        return MagicMock(returncode=0)
                    return MagicMock(returncode=1, stderr='error')
                mock_run.side_effect = side_effect
                assert gp.upload_to_gdrive(test_file) is None


class TestShareFilePublic:
    """Tests for share_file_public()"""

    def test_returns_url_even_on_failure(self):
        with patch.object(gp, '_check_rclone', return_value=(True, None)):
            with patch('subprocess.run', return_value=MagicMock(returncode=1, stdout='[]')):
                url = gp.share_file_public('file123')
                assert 'file123' in url

    def test_returns_none_when_rclone_missing(self):
        with patch.object(gp, '_check_rclone', return_value=(False, 'error')):
            assert gp.share_file_public('file123') is None

    def test_calls_rclone_link_with_filename(self):
        lsjson_result = MagicMock(returncode=0, stdout=json.dumps([
            {'Name': 'test.m3u', 'ID': 'file123'}
        ]))
        link_result = MagicMock(returncode=0, stdout='https://drive.google.com/open?id=file123')
        with patch.object(gp, '_check_rclone', return_value=(True, None)):
            with patch('subprocess.run', side_effect=[lsjson_result, link_result]) as mock_run:
                url = gp.share_file_public('file123')
                assert 'file123' in url
                # Second call should be rclone link with the filename
                assert mock_run.call_count == 2
                assert 'link' in mock_run.call_args_list[1][0][0]


class TestUploadAndRegister:
    """Tests for upload_and_register()"""

    def test_returns_failure_when_upload_fails(self, tmp_path):
        test_file = tmp_path / 'test.m3u'
        test_file.write_text('#EXTM3U\n')
        with patch.object(gp, 'upload_to_gdrive', return_value=None):
            result = gp.upload_and_register(test_file, 'test')
            assert result['uploaded'] is False

    def test_full_success_flow(self, tmp_path):
        test_file = tmp_path / 'test.m3u'
        test_file.write_text('#EXTM3U\n')
        with patch.object(gp, 'upload_to_gdrive', return_value={'file_id': 'f1', 'url': 'http://dl'}):
            with patch.object(gp, 'share_file_public', return_value='http://shared'):
                with patch.object(gp, 'add_playlist_to_iptvx', return_value=True):
                    result = gp.upload_and_register(test_file, 'test')
                    assert result['uploaded'] is True
                    assert result['iptvx_updated'] is True

    def test_needs_manual_add_when_iptvx_not_found(self, tmp_path):
        test_file = tmp_path / 'test.m3u'
        test_file.write_text('#EXTM3U\n')
        with patch.object(gp, 'upload_to_gdrive', return_value={'file_id': 'f1', 'url': 'http://dl'}):
            with patch.object(gp, 'share_file_public', return_value='http://shared'):
                with patch.object(gp, 'add_playlist_to_iptvx', return_value=False):
                    result = gp.upload_and_register(test_file, 'test')
                    assert result['needs_manual_add'] is True


class TestParseMovieFile:
    """Tests for parse_movie_file()"""

    def test_parses_csv_with_all_columns(self, tmp_path):
        csv_file = tmp_path / "movies.csv"
        csv_file.write_text(
            "title,title_en,year,genre,director,imdb_search_url\n"
            "Comme le feu,Who by Fire,2024,Drama,Philippe Lesage,http://imdb.com\n"
            "The G,,2024,Thriller,Karl R. Hearne,http://imdb.com\n"
        )
        result = gp.parse_movie_file(str(csv_file))
        assert len(result) == 2
        assert result[0]["title"] == "Comme le feu"
        assert result[0]["title_en"] == "Who by Fire"
        assert result[0]["year"] == "2024"
        assert result[1]["title_en"] == ""

    def test_skips_empty_rows(self, tmp_path):
        csv_file = tmp_path / "movies.csv"
        csv_file.write_text(
            "title,title_en,year\n"
            ",,\n"
            "Real Movie,,2024\n"
        )
        result = gp.parse_movie_file(str(csv_file))
        assert len(result) == 1
        assert result[0]["title"] == "Real Movie"

    def test_missing_year_returns_none(self, tmp_path):
        csv_file = tmp_path / "movies.csv"
        csv_file.write_text(
            "title,title_en,year\n"
            "Some Movie,Some Movie EN,\n"
        )
        result = gp.parse_movie_file(str(csv_file))
        assert result[0]["year"] is None

    def test_exits_on_missing_file(self):
        with pytest.raises(SystemExit):
            gp.parse_movie_file("/nonexistent/file.csv")


class TestMoviesFromFile:
    """Tests for movies_from_file() and file-sourced pipeline"""

    def setup_method(self):
        self.config = {
            "iptv_server": "http://server.com",
            "iptv_username": "user",
            "iptv_password": "pass",
            "output_dir": "playlists",
        }
        self.vod_index = {
            "who by fire": {
                "name": "Who by Fire",
                "stream_id": 123,
                "container_extension": "mp4",
                "stream_icon": "http://icon.jpg",
                "year": "2024",
                "_source": {"name": "provider1", "server": "http://server.com", "username": "user", "password": "pass"},
            },
            "the g": {
                "name": "The G",
                "stream_id": 456,
                "container_extension": "mkv",
                "stream_icon": "",
                "year": "2024",
                "_source": {"name": "provider1", "server": "http://server.com", "username": "user", "password": "pass"},
            },
        }

    def _generate(self, parsed, name="Test"):
        movies = gp.movies_from_file(parsed, name)
        header = [f"# @name: {name}", "# @source_type: file"]
        return gp.generate_m3u(name, movies, self.config, self.vod_index, header_lines=header)

    def test_matches_by_english_title(self):
        content, stats = self._generate(
            [{"title": "Comme le feu", "title_en": "Who by Fire", "year": "2024"}],
            "Quebec-Movies",
        )
        assert stats["matched"] == 1
        assert stats["unmatched"] == 0
        assert "stream_id: 123" in content
        assert "Quebec-Movies" in content

    def test_matches_by_original_title_first(self):
        self.vod_index["comme le feu"] = {
            "name": "Comme le feu",
            "stream_id": 789,
            "container_extension": "mp4",
            "stream_icon": "",
            "_source": {"name": "p1", "server": "http://s.com", "username": "u", "password": "p"},
        }
        content, stats = self._generate(
            [{"title": "Comme le feu", "title_en": "Who by Fire", "year": "2024"}],
        )
        assert stats["matched"] == 1
        assert "stream_id: 789" in content

    def test_unmatched_movie(self):
        content, stats = self._generate(
            [{"title": "Nonexistent Movie", "title_en": "", "year": "2024"}],
        )
        assert stats["matched"] == 0
        assert stats["unmatched"] == 1
        assert "unmatched" in content

    def test_m3u_header_contains_metadata(self):
        content, stats = self._generate(
            [{"title": "The G", "title_en": "", "year": "2024"}],
            "Quebec-Movies",
        )
        assert "#EXTM3U" in content
        assert "# @name: Quebec-Movies" in content
        assert "# @source_type: file" in content

    def test_includes_english_title_comment(self):
        content, stats = self._generate(
            [{"title": "Comme le feu", "title_en": "Who by Fire", "year": "2024"}],
        )
        assert "# @title_en: Who by Fire" in content

    def test_empty_movie_list(self):
        content, stats = self._generate([])
        assert stats["matched"] == 0
        assert stats["unmatched"] == 0
        assert "#EXTM3U" in content


class TestMoviesFromVideoData:
    """Tests for movies_from_video_data()"""

    def test_converts_video_data_to_common_format(self):
        video_data = [{
            'video_id': 'vid1',
            'title': 'Top 10 Horror',
            'movies': [
                {'name': 'Movie A', 'year': '2020'},
                {'name': 'Movie B'},
            ]
        }]
        result = gp.movies_from_video_data(video_data)
        assert len(result) == 2
        assert result[0]['name'] == 'Movie A'
        assert result[0]['year'] == '2020'
        assert result[0]['group'] == 'Top 10 Horror'
        assert result[0]['search_titles'] == ['Movie A']
        assert any('vid1' in ml for ml in result[0]['meta_lines'])

    def test_empty_video_data(self):
        assert gp.movies_from_video_data([]) == []


class TestFetchSeriesCatalog:
    """Tests for fetch_series_catalog()"""

    def test_fetches_and_parses_series(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        series_json = json.dumps([
            {'name': 'Show (2020)', 'title': 'Show', 'series_id': 100, 'year': '2020'},
        ])

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('builtins.open', mock_open(read_data=series_json)):
                with patch('os.path.exists', return_value=True):
                    with patch('os.unlink'):
                        result = gp.fetch_series_catalog(config)
                        assert len(result) == 1
                        assert result[0]['series_id'] == 100

    def test_returns_empty_on_failure(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with patch('os.path.exists', return_value=True):
                with patch('os.unlink'):
                    result = gp.fetch_series_catalog(config)
                    assert result == []


class TestFetchMultiSeriesCatalog:
    """Tests for fetch_multi_series_catalog()"""

    def test_merges_catalogs_with_source(self):
        configs = [
            {'iptv_server': 'http://a.com', 'iptv_username': 'u1', 'iptv_password': 'p1', '_name': 'prov1'},
        ]
        with patch.object(gp, 'fetch_series_catalog', return_value=[
            {'name': 'Show A', 'series_id': 1},
        ]):
            result = gp.fetch_multi_series_catalog(configs)
            assert len(result) == 1
            assert result[0]['_source']['name'] == 'prov1'


class TestFetchSeriesEpisodes:
    """Tests for fetch_series_episodes()"""

    def test_parses_episodes(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        series_info = json.dumps({
            'episodes': {
                '1': [
                    {'id': 10, 'title': 'Pilot', 'season': 1, 'episode_num': 1, 'container_extension': 'mkv'},
                    {'id': 11, 'title': 'Ep 2', 'season': 1, 'episode_num': 2, 'container_extension': 'mkv'},
                ],
            }
        })
        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout=series_info)):
            result = gp.fetch_series_episodes(config, 100)
            assert len(result) == 2
            assert result[0]['id'] == 10
            assert result[0]['season'] == 1
            assert result[0]['container_extension'] == 'mkv'

    def test_returns_empty_on_error(self):
        config = {'iptv_server': 'http://s.com', 'iptv_username': 'u', 'iptv_password': 'p'}
        with patch('subprocess.run', return_value=MagicMock(returncode=1, stdout='')):
            assert gp.fetch_series_episodes(config, 999) == []


class TestBuildSeriesStreamUrl:
    """Tests for build_series_stream_url()"""

    def test_builds_url_from_source(self):
        series = {'_source': {'server': 'http://s.com', 'username': 'u', 'password': 'p'}}
        url = gp.build_series_stream_url(series, 42, 'mkv')
        assert url == 'http://s.com/series/u/p/42.mkv'


class TestParseMovieFileStartYear:
    """Tests for parse_movie_file() with start_year column (TV shows)"""

    def test_normalizes_start_year_to_year(self, tmp_path):
        csv_file = tmp_path / "shows.csv"
        csv_file.write_text(
            "title,start_year,end_year,genre\n"
            "Lance et compte,1986,2015,Drame sportif\n"
        )
        result = gp.parse_movie_file(str(csv_file))
        assert len(result) == 1
        assert result[0]["year"] == "1986"
        assert result[0]["genre"] == "Drame sportif"

    def test_preserves_all_columns(self, tmp_path):
        csv_file = tmp_path / "shows.csv"
        csv_file.write_text(
            "title,start_year,end_year,genre\n"
            "Show A,2020,2023,Comédie\n"
        )
        result = gp.parse_movie_file(str(csv_file))
        assert result[0]["start_year"] == "2020"
        assert result[0]["end_year"] == "2023"
        assert result[0]["genre"] == "Comédie"


class TestMoviesFromFileGroupBy:
    """Tests for movies_from_file() group_by with multiple columns"""

    def test_group_by_single_column(self):
        parsed = [{"title": "Movie", "year": "2024", "genre": "Drame"}]
        entries = gp.movies_from_file(parsed, "Test", group_by="year")
        assert len(entries) == 1
        assert entries[0]["group"] == "2024"

    def test_group_by_multiple_creates_duplicates(self):
        parsed = [{"title": "Movie", "year": "2024", "genre": "Drame"}]
        entries = gp.movies_from_file(parsed, "Test", group_by=["year", "genre"])
        assert len(entries) == 2
        groups = {e["group"] for e in entries}
        assert groups == {"2024", "Drame"}

    def test_fallback_to_playlist_name(self):
        parsed = [{"title": "Movie"}]
        entries = gp.movies_from_file(parsed, "Fallback", group_by=["year"])
        assert entries[0]["group"] == "Fallback"
