"""Regressions for real media streams, cache integrity and unattended production."""
import asyncio
import json
import math
import os
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from studio_audio import make_audio, phrase_cues, validate_audio, write_srt
from studio_automation import generate_researched_draft
from worker import render_scene, stitch, video_details, safe_publish_error, save_fields, usable_footage, run
from studio_publish import publication_due
from studio_revision import stitch_revision


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True, timeout=90)


def energy(path, hz, start=0, seconds=.5):
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path),
        '-t', str(seconds), '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'])
    values = struct.unpack('<' + 'f' * (len(data) // 4), data)
    return abs(sum(v * complex(math.cos(2*math.pi*hz*i/8000), math.sin(2*math.pi*hz*i/8000)) for i,v in enumerate(values)))


class MediaRegression(unittest.TestCase):
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
            ffmpeg('-f', 'lavfi', '-i', 'color=blue:s=320x180:r=30', '-frames:v', 30,
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
