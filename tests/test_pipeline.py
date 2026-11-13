"""Regressions for real media streams, cache integrity and unattended production."""
import asyncio
import json
import math
import os
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from googleapiclient.errors import HttpError
from studio_audio import make_audio, phrase_cues, retime_srt, validate_audio, write_srt
from studio_automation import generate_researched_draft
from worker import render_scene, scene_durations, stitch, verify_render_geometry, video_details, safe_publish_error, save_fields, usable_footage, run
from studio_publish import publication_due, thumbnail_due
from studio_revision import stitch_revision

from studio_retry import retry_io


class UploadRegression(unittest.TestCase):
    def test_youtube_metadata_preserves_source_urls_within_byte_limit(self):
        from worker import youtube_snippet
        sources = "\n\nSources\nJournal — https://example.org/study"
        snippet = youtube_snippet({"title": "<AI> Study", "description": "é" * 3000 + sources,
                                   "tags": ["learning", "learning", "x" * 510, "health"]})
        self.assertEqual(snippet["title"], "AI Study")
        self.assertTrue(snippet["description"].endswith(sources))
        self.assertLessEqual(len(snippet["description"].encode()), 5000)
        self.assertEqual(snippet["tags"], ["learning", "health"])

    def test_youtube_chapters_use_rendered_timestamps_and_keep_sources(self):
        from worker import youtube_chapters, youtube_filename, youtube_snippet
        scenes = [{"chapter_title": "The study question"}, {}, {"chapter_title": "The findings"},
                  {"chapter_title": "Putting it into practice"}]
        timeline = [{"scene_number": i + 1, "start": t} for i, t in enumerate([0, 8, 143.75, 281.4])]
        chapters = youtube_chapters(scenes, timeline, 695)
        self.assertEqual([chapter["start"] for chapter in chapters], [0, 143, 281])
        snippet = youtube_snippet({"title": "The Study", "description": "é" * 3000 + "\n\nSources\nhttps://example.org/study",
                                  "youtube_chapters": chapters})
        self.assertIn("00:00 The study question\n02:23 The findings\n04:41 Putting it into practice", snippet["description"])
        self.assertTrue(snippet["description"].endswith("https://example.org/study"))
        self.assertLessEqual(len(snippet["description"].encode()), 5000)
        self.assertEqual(youtube_filename("À study about AI & health"), "a-study-about-ai-health.mp4")

    def test_youtube_upload_schedule_is_explicit_and_late_upload_stops(self):
        from datetime import datetime, timezone
        from worker import PublishTimePassed, youtube_upload_status
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        self.assertEqual(youtube_upload_status({}, now)["privacyStatus"], "public")
        future = youtube_upload_status({"publish_at": "2026-11-13T08:00:00+01:00"}, now)
        self.assertEqual(future["privacyStatus"], "private")
        self.assertEqual(future["publishAt"], "2026-11-13T07:00:00Z")
        with self.assertRaises(PublishTimePassed):
            youtube_upload_status({"publish_at": "2026-11-12T08:00:00Z"}, now)

    @patch("worker.time.sleep")
    @patch("worker.MediaFileUpload")
    @patch("worker.youtube_credentials")
    @patch("worker.build")
    def test_lost_response_after_upload_session_started_requires_reconciliation(self, build, credentials, media, sleep):
        from worker import UploadOutcomeUncertain, upload_youtube
        request = build.return_value.videos.return_value.insert.return_value
        request.resumable_uri = "fixture"
        request.next_chunk.side_effect = TimeoutError()
        with self.assertRaises(UploadOutcomeUncertain) as result:
            upload_youtube({"title": "Saved production"}, Path("fixture.mp4"))
        self.assertEqual(safe_publish_error(result.exception)[0], "upload_uncertain")
        self.assertEqual(request.next_chunk.call_count, 4)


class ThumbnailRetryRegression(unittest.TestCase):
    @patch("worker.verify_thumbnail")
    @patch("worker.save_fields")
    @patch("worker.set_youtube_thumbnail")
    def test_reports_thumbnail_error_separately_from_the_video_upload(self, set_thumbnail, save, verify):
        from worker import apply_uploaded_thumbnail
        set_thumbnail.side_effect = HttpError(
            SimpleNamespace(status=403, reason="Forbidden"),
            b'{"error":{"message":"Forbidden","errors":[{"reason":"forbidden"}]}}',
        )
        row = {"id": "already-uploaded"}
        self.assertFalse(apply_uploaded_thumbnail(row, object(), "AbCdEf12345", Path("cover.jpg")))
        self.assertEqual(save.call_args.kwargs["thumbnail_publish_status"], "failed")
        self.assertIn("HTTP 403 forbidden", save.call_args.kwargs["progress_note"])
        self.assertNotIn("publish_status", save.call_args.kwargs)

    @patch("worker.verify_thumbnail")
    @patch("worker.save_fields")
    @patch("worker.set_youtube_thumbnail")
    def test_processing_delay_schedules_only_thumbnail_retry(self, set_thumbnail, save, verify):
        from datetime import datetime, timedelta, timezone
        from worker import apply_uploaded_thumbnail
        set_thumbnail.side_effect = HttpError(SimpleNamespace(status=404, reason="Not Found"),
            b'{"error":{"message":"Not found","errors":[{"reason":"videoNotFound"}]}}')
        now = datetime.now(timezone.utc)
        row = {"id": "uploaded", "status": "completed", "scenes": [{"_studio": {
            "publish_status": "uploaded", "publish_approved_at": now.isoformat(), "publish_approved_by": "owner",
            "youtube_id": "AbCdEf12345", "thumbnail_url": "https://example.org/cover.jpg",
        }}]}
        self.assertFalse(apply_uploaded_thumbnail(row, object(), "AbCdEf12345", Path("cover.jpg")))
        row["scenes"][0]["_studio"].update(save.call_args.kwargs)
        self.assertFalse(thumbnail_due(row, "auth", now))
        self.assertTrue(thumbnail_due(row, "auth", now + timedelta(minutes=5)))
        self.assertFalse(publication_due(row, "auth", now + timedelta(minutes=5)))


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True, timeout=90)


def energy(path, hz, start=0, seconds=.5):
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path),
        '-t', str(seconds), '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'])
    values = struct.unpack('<' + 'f' * (len(data) // 4), data)
    return abs(sum(v * complex(math.cos(2*math.pi*hz*i/8000), math.sin(2*math.pi*hz*i/8000)) for i,v in enumerate(values)))


class ExternalRetryRegression(unittest.TestCase):
    def test_storage_retry_recovers_from_service_error_and_skips_permanent_errors(self):
        from unittest.mock import Mock
        from storage3.exceptions import StorageApiError
        outage = StorageApiError("temporary", "unavailable", 503)
        service = Mock(side_effect=[outage, "saved"])
        with patch("studio_retry.time.sleep"):
            self.assertEqual(retry_io(service, "save preview"), "saved")
        self.assertEqual(service.call_count, 2)

        denied = Mock(side_effect=StorageApiError("denied", "forbidden", 403))
        with self.assertRaises(StorageApiError):
            retry_io(denied, "save preview")
        self.assertEqual(denied.call_count, 1)


class MediaRegression(unittest.TestCase):
    def test_requested_runtime_retimes_audio_and_captions_in_both_formats(self):
        self.assertEqual(sum(round(d * 30) for d in scene_durations([60.8, 61.9, 60.2], 180)), 5400)
        self.assertEqual(sum(round(d * 30) for d in scene_durations([234.1, 239.9, 242.7], 720)), 21600)
        with self.assertRaisesRegex(ValueError, "Adjust the script length"):
            scene_durations([15], 720)
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source, audio, srt = directory/"footage.mp4", directory/"voice.wav", directory/"voice.srt"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-t", "1", "-c:v", "libx264", source)
            ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2.4", audio)
            write_srt([(0.6, 1.2, "This caption follows the voice.")], srt)
            timed = retime_srt(srt, directory/"timed.srt", 1.2, 2)
            self.assertIn("00:00:00,500 --> 00:00:01,000", timed.read_text())
            for ratio, size in [("9:16", (144, 256)), ("16:9", (256, 144))]:
                clip = render_scene({"scene_number": 1, "format": ratio}, source, audio, timed, directory,
                                    duration=2, size=size, tempo=1.2)
                final = stitch([clip], directory)
                self.assertEqual(verify_render_geometry(final, size, 2), (ratio, 2.0))
                self.assertGreater(energy(final, 440, start=1.45), energy(final, 880, start=1.45) * 10)

    def test_approximate_runtime_respects_narration_and_short_cap(self):
        self.assertAlmostEqual(sum(scene_durations([260, 265, 245], 720, approximate=True)), 770)
        self.assertAlmostEqual(sum(scene_durations([140, 150], 720, approximate=True)), 322.2333, places=3)
        self.assertAlmostEqual(sum(scene_durations([100, 98], 180, approximate=True, ceiling=180)), 180)

    def test_narration_beats_stock_audio_and_survives_all_cuts(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            red, blue = directory/'red.mp4', directory/'blue.mp4'
            # This stereo stock track used to override the mono narration.
            for path, color in ((red, 'red'), (blue, 'blue')):
                ffmpeg('-f','lavfi','-i',f'color={color}:s=320x180:r=30',
                    '-f','lavfi','-i','sine=frequency=880:sample_rate=48000',
                    '-t','1.6','-ac','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',path)
            rendered = []
            for number, hz in ((1,440),(2,660)):
                audio, srt = directory/f'voice{number}.wav', directory/f'voice{number}.srt'
                ffmpeg('-f','lavfi','-i',f'sine=frequency={hz}:sample_rate=24000','-t','1.6','-ac','1',audio)
                write_srt([(0,1.6,'Narration survives the cut.')],srt)
                output = render_scene({'scene_number':number,'format':'16:9'},[red,blue],audio,srt,directory)
                self.assertGreater(energy(output,hz), energy(output,880)*15)
                rendered.append(output)
            final = stitch(rendered,directory)
            validate_audio(final,expected=3.2)
            self.assertGreater(energy(final,440,start=.2), energy(final,880,start=.2)*15)
            self.assertGreater(energy(final,660,start=1.9), energy(final,880,start=1.9)*15)
            # A new shot really appears; no long frozen frame.
            from PIL import Image
            import io
            colors=[]
            for point in (.2,1.0):
                png=subprocess.check_output(['ffmpeg','-v','error','-ss',str(point),'-i',str(rendered[0]),'-frames:v','1','-f','image2pipe','-vcodec','png','-threads','1','-'])
                colors.append(Image.open(io.BytesIO(png)).getpixel((100,100)))
            self.assertGreater(colors[0][0],colors[0][2]+100)
            self.assertGreater(colors[1][2],colors[1][0]+100)
            self.assertIn(b'moov',final.read_bytes()[:10000])

    def test_neural_retry_rejects_partial_audio_and_preserves_timing(self):
        with TemporaryDirectory() as tmp:
            directory=Path(tmp)
            calls=[]
            async def fake(text,path):
                calls.append(text)
                if len(calls)==1:
                    path.write_bytes(b'partial audio')
                    raise ConnectionError('interrupted')
                ffmpeg('-f','lavfi','-i','sine=frequency=440:sample_rate=24000','-t','2',path)
                return [(i * .6, (i + 1) * .6, word) for i, word in enumerate(text.split())]
            scene={'scene_number':1,'narration':'Hello world'}
            with patch('studio_audio.synthesize',fake), patch('studio_audio.time.sleep'):
                audio,srt=make_audio(scene,directory)
                self.assertEqual(len(calls),2)
                self.assertIn('00:00:01,300',srt.read_text())
                make_audio(scene,directory)
                self.assertEqual(len(calls),2)
                make_audio({**scene,'narration':'New narration'},directory)
                self.assertEqual(len(calls),3)
                validate_audio(audio)

    def test_failed_full_scene_recovers_in_same_voice_with_continuous_captions(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            calls = []
            line = "These are the words in the first phrase. These are all the words in the second phrase."
            async def fake(text, path):
                calls.append(text)
                if text == line or text == "These are all the words in the second phrase.":
                    raise ValueError("fixture timing error")
                ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "2.2", path)
                return [(i * .2, (i + 1) * .2, word) for i, word in enumerate(text.split())]
            with patch("studio_audio.synthesize", fake), patch("studio_audio.time.sleep"):
                audio, srt = make_audio({"scene_number": 9, "narration": line}, directory)
            self.assertEqual(calls[:2], [line, line])
            self.assertEqual(calls.count("These are all the words in the second phrase."), 2)
            self.assertGreater(validate_audio(audio), 4.3)
            self.assertIn("00:00:02,", srt.read_text())
            captions = " ".join(line for line in srt.read_text().splitlines() if line and "-->" not in line and not line.isdecimal())
            self.assertEqual(captions, line)

    def test_audio_repair_preserves_picture_and_surrounding_narration(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / 'stock.mp4'
            ffmpeg('-f', 'lavfi', '-i', 'color=blue:s=320x180:r=30', '-t', '2', '-c:v', 'libx264', source)
            rendered = []
            for number, hz in ((1, 440), (2, 660), (3, 550)):
                audio, srt = directory / f'voice{number}.wav', directory / f'voice{number}.srt'
                ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={hz}:sample_rate=48000', '-t', '1.6', audio)
                write_srt([(0, 1.6, 'Keep the picture.')], srt)
                rendered.append(render_scene({'scene_number': number}, source, audio, srt, directory))
            parent = stitch(rendered, directory).rename(directory / 'parent.mp4')
            replacement = directory / 'repair.wav'
            ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=770:sample_rate=48000', '-t', '1.6', replacement)
            ffmpeg('-i', replacement, '-ac', '2', directory / 'repair_stereo.wav')
            revised = stitch_revision(parent, 48, 96, [], [directory / 'repair_stereo.wav'], "audio", directory)
            self.assertGreater(energy(revised, 440, start=.3), energy(revised, 770, start=.3) * 10)
            self.assertGreater(energy(revised, 770, start=1.9), energy(revised, 660, start=1.9) * 10)
            self.assertGreater(energy(revised, 550, start=3.5), energy(revised, 770, start=3.5) * 10)
            def picture(path):
                return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '2.3', '-i', str(path),
                    '-vf', 'scale=1:1', '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-'])
            self.assertTrue(all(abs(a-b) <= 3 for a,b in zip(picture(parent), picture(revised))))

    def test_visual_revision_copies_approved_frames_and_entire_narration(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            parent = folder / 'parent.mp4'
            replacement = folder / 'replacement.mp4'
            ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                   '-t', '3', '-c:v', 'libx264', '-bf', '0', '-g', '30',
                   '-video_track_timescale', '90000', '-ac', '2', '-c:a', 'aac', parent)
            ffmpeg('-f', 'lavfi', '-i', 'color=blue:s=320x180:r=30', '-frames:v', 29,
                   '-c:v', 'libx264', '-bf', '0', '-video_track_timescale', '90000', replacement)
            revised = stitch_revision(parent, 30, 60, [replacement], [], "visual", folder)
            def frame_hashes(path):
                return [line.split(',')[-1].strip() for line in subprocess.check_output(
                    ['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'framemd5', '-'],
                    text=True).splitlines() if not line.startswith('#')]
            before, after = frame_hashes(parent), frame_hashes(revised)
            self.assertEqual(before[:30], after[:30])
            self.assertEqual(before[60:], after[60:])
            self.assertNotEqual(before[30:60], after[30:60])
            def audio_packet_hashes(path):
                result = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                    '-show_packets', '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256',
                    '-of', 'json', str(path)])
                return [packet["data_hash"] for packet in json.loads(result)["packets"]]
            self.assertEqual(audio_packet_hashes(parent), audio_packet_hashes(revised))

    def test_twelve_minute_join_keeps_continuous_narration(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            clips = []
            for name, frequency in [('a', 440), ('b', 660)]:
                clip = directory / (name + '.mp4')
                ffmpeg('-f', 'lavfi', '-i', 'color=blue:s=320x180:r=30', '-t', '12',
                       '-an', '-c:v', 'libx264', '-video_track_timescale', '90000', clip)
                ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000',
                       '-t', '12', '-ac', '2', clip.with_suffix('.wav'))
                clips.append(clip)
            final = stitch(clips * 30, directory)
            validate_audio(final, expected=720)
            for index in (0, 1, 29, 30, 58, 59):
                start = index * 12 + .1
                expected, other = (440, 660) if index % 2 == 0 else (660, 440)
                self.assertGreater(energy(final, expected, start=start), energy(final, other, start=start) * 10)

    def test_stitch_recovers_from_non_monotonic_picture_timestamps(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            clips = []
            frames = (24, 37, 32)
            for number, count in enumerate(frames):
                clip = directory / f'clip{number}.mp4'
                ffmpeg('-f', 'lavfi', '-i', 'color=blue:s=320x180:r=30', '-frames:v', count,
                       '-an', '-c:v', 'libx264', '-colorspace', ('bt709' if number % 2 else 'bt470bg'),
                       '-video_track_timescale', '90000', clip)
                ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                       '-t', f'{count / 30:.6f}', '-ac', '2', clip.with_suffix('.wav'))
                clips.append(clip)
            def mux(command, **options):
                if command[command.index('-c:v') + 1] == 'copy':
                    raise RuntimeError('FFmpeg exited: Non-monotonic DTS')
                return run(command, **options)
            with patch('worker.run', side_effect=mux):
                final = stitch(clips, directory)
            validate_audio(final, expected=sum(frames) / 30)
            self.assertGreater(energy(final, 440, start=2), energy(final, 660, start=2) * 10)

    def test_captions_break_at_clauses_and_pauses(self):
        marks = [(0,.3,'Here'),(.3,.5,'it'),(.5,.9,'is,'),(1,1.25,'one'),(1.26,1.6,'example.'),(2.1,2.4,'Done.')]
        self.assertEqual([cue[2] for cue in phrase_cues(marks, 2.5)], ['Here it is,', 'one example.', 'Done.'])

    def test_green_screen_stock_is_rejected(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'keyed.mp4'
            ffmpeg('-f','lavfi','-i','color=0x00ff00:s=320x180:r=30','-t','2','-c:v','libx264','-pix_fmt','yuv420p',source)
            self.assertFalse(usable_footage(source))
            ffmpeg('-f','lavfi','-i','color=0x14232b:s=320x180:r=30','-t','2','-c:v','libx264','-pix_fmt','yuv420p',source)
            self.assertTrue(usable_footage(source))

    def test_silent_audio_is_rejected(self):
        with TemporaryDirectory() as tmp:
            audio=Path(tmp)/'silent.wav'
            ffmpeg('-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1',audio)
            with self.assertRaisesRegex(ValueError,'silent'):
                validate_audio(audio)


class PipelineRegression(unittest.TestCase):
    def test_saved_settings_beat_conflicting_database_defaults(self):
        _, ratio, details = video_details({"format": "16:9", "duration_seconds": 180, "scenes": [
            {"narration": "Keep the selected settings.", "_studio": {"aspect_ratio": "9:16", "duration_seconds": 60}},
        ]})
        self.assertEqual(ratio, "9:16")
        self.assertEqual(details["duration_seconds"], 60)
    def test_legacy_metadata_keeps_ratio_and_every_word(self):
        narration='This is a complete sentence. '*25
        scenes,ratio,details=video_details({'title':'Saved job','scenes':[{'narration':narration,
            'search_queries':'hospital,doctor','_studio':{'aspect_ratio':'9:16','duration_seconds':90}}], 'format':None})
        self.assertEqual(ratio,'9:16')
        self.assertEqual(' '.join(s['narration'] for s in scenes),narration.strip())
        self.assertTrue(all(len(s['narration'].split())<=40 for s in scenes))
        self.assertEqual(scenes[0]['search_queries'],['hospital','doctor'])

    def test_daily_generation_uses_shared_research_engine(self):
        from subprocess import CompletedProcess
        with patch('studio_automation.subprocess.run', return_value=CompletedProcess([], 0, '{"title":"A researched topic"}', '')) as run:
            draft = generate_researched_draft('Study methods', 60, '9:16', ['Previous video'])
            self.assertEqual(draft['title'], 'A researched topic')
            args, kwargs = run.call_args
            self.assertTrue(args[0][-1].endswith('src/lib/studio-daily.ts'))
            self.assertEqual(json.loads(kwargs['input'])['recent'], ['Previous video'])

    def test_auth_failure_gives_reconnect_action(self):
        state,note=safe_publish_error(RuntimeError('invalid_grant expired or revoked'))
        self.assertEqual(state,'auth_required')
        self.assertIn('Reconnect YouTube',note)

    def test_progress_and_upload_state_persist_in_actual_schema(self):
        class Query:
            data=[{'id':'one'}]
            def update(self,payload): self.payload=payload; return self
            def eq(self,*args): return self
            def execute(self): return self
        class Client:
            query=Query()
            def table(self,name): return self.query
        row={'id':'one','status':'completed','scenes':[{'narration':'Hello','_studio':{'aspect_ratio':'9:16'}}]}
        save_fields(Client(),row,publish_status='auth_required',progress_note='Reconnect')
        self.assertEqual(row['scenes'][0]['_studio']['publish_status'],'auth_required')
        self.assertEqual(row['scenes'][0]['_studio']['aspect_ratio'],'9:16')
        self.assertIn('worker_heartbeat_at',row['scenes'][0]['_studio'])

    def test_saved_upload_retries_once_after_authorization_changes(self):
        from datetime import datetime, timezone

        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        meta = {"publish_status": "auth_required", "publish_auth_version": "previous",
                "publish_attempts": 5, "video_url": "https://storage.example/video.mp4"}
        row = {"status": "completed", "scenes": [{"_studio": meta}]}
        self.assertFalse(publication_due(row, "", now))
        self.assertFalse(publication_due(row, "previous", now))
        self.assertFalse(publication_due(row, "replacement", now))
        meta.update(publish_approved_at=now.isoformat(), publish_approved_by="owner")
        self.assertFalse(publication_due(row, "previous", now))
        self.assertTrue(publication_due(row, "replacement", now))
        meta["youtube_video_id"] = "existing-upload"
        self.assertFalse(publication_due(row, "replacement", now))

    def test_interrupted_upload_is_flagged_without_automatic_reupload(self):
        from datetime import datetime, timezone

        now = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)
        meta = {"publish_status": "uploading", "worker_heartbeat_at": "2026-11-12T09:55:00Z",
                "publish_approved_at": now.isoformat(), "publish_approved_by": "owner",
                "video_url": "https://storage.example/video.mp4"}
        row = {"status": "completed", "scenes": [{"_studio": meta}]}
        self.assertFalse(publication_due(row, "credential", now))
        meta["worker_heartbeat_at"] = "2026-11-12T07:00:00Z"
        self.assertTrue(publication_due(row, "credential", now))
        meta["publish_status"] = "upload_uncertain"
        self.assertFalse(publication_due(row, "credential", now))
        meta["publish_requested"] = True
        self.assertTrue(publication_due(row, "credential", now))

    def test_saved_render_waits_for_user_approval_even_with_working_credentials(self):
        from datetime import datetime, timezone

        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        meta = {"publish_status": "awaiting_review", "video_url": "https://storage.example/video.mp4"}
        row = {"status": "completed", "scenes": [{"_studio": meta}]}
        self.assertFalse(publication_due(row, "credential", now))
        meta["publish_requested"] = True
        self.assertFalse(publication_due(row, "credential", now))
        meta.update(publish_approved_at=now.isoformat(), publish_approved_by="owner")
        self.assertTrue(publication_due(row, "credential", now))

if __name__=='__main__': unittest.main()
