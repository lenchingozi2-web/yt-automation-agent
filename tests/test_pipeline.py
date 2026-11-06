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

from studio_audio import make_audio, validate_audio, write_srt
from studio_automation import generate_json, validate_script
from worker import render_scene, stitch, video_details, safe_publish_error, save_fields


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
                return [(0,.5,'Hello'),(.6,1.2,'world')]
            scene={'scene_number':1,'narration':'Hello world'}
            with patch('studio_audio.synthesize',fake), patch('studio_audio.time.sleep'):
                audio,srt=make_audio(scene,directory)
                self.assertEqual(len(calls),2)
                self.assertIn('00:00:01,200',srt.read_text())
                make_audio(scene,directory)
                self.assertEqual(len(calls),2)
                make_audio({**scene,'narration':'New narration'},directory)
                self.assertEqual(len(calls),3)
                validate_audio(audio)

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

    def test_generator_falls_back_only_to_chat_models(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':{'content':'{"title":"A useful topic"}'}}]}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'','GROQ_API_KEY':'dummy'}), patch('studio_automation.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=Response()
            result=generate_json('Return a JSON script')
            request=client.return_value.__enter__.return_value.post.call_args.kwargs
            self.assertNotIn('whisper',request['json']['model'])
            self.assertEqual(result['title'],'A useful topic')

    def test_missing_narration_fails_validation(self):
        with self.assertRaisesRegex(ValueError,'no narration'):
            validate_script({'title':'test','scenes':[{'narration':'','search_queries':['doctor']}]*3},60,'9:16')

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

if __name__=='__main__': unittest.main()
