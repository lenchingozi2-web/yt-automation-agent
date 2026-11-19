"""Measure overlap and validate that concurrency does not change render results."""
import hashlib
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from studio_pipeline import scene_pipeline, render_parallelism
import worker


class ScenePipelineTests(unittest.TestCase):
    def test_order_and_overlap(self):
        started=threading.Event()
        release=threading.Event()
        def encode(value):
            if value==0:
                started.set()
                if not release.wait(3):
                    raise RuntimeError("producer did not overlap")
            return value
        with scene_pipeline(2) as pipeline:
            pipeline.submit(encode,"first",0)
            self.assertTrue(started.wait(2))
            pipeline.submit(encode,"second",1)
            release.set()
            self.assertEqual(list(pipeline.drain()),[(0,"first"),(1,"second")])

    def test_capacity_and_error_propagation(self):
        def fail():
            raise ValueError("encode failed")
        with scene_pipeline(1) as pipeline:
            pipeline.submit(fail,None)
            pipeline.submit(lambda: 1,None)
            self.assertTrue(pipeline.full())
            with self.assertRaisesRegex(RuntimeError,"capacity"):
                pipeline.submit(lambda: 2,None)
            with self.assertRaisesRegex(ValueError,"encode failed"):
                pipeline.pop()

    def test_cpu_limits(self):
        with patch("studio_pipeline.os.cpu_count",return_value=2),patch.dict("os.environ",{"STUDIO_RENDER_PARALLELISM":"10"}):
            self.assertEqual(render_parallelism(),1)
        with patch("studio_pipeline.os.cpu_count",return_value=8),patch.dict("os.environ",{"STUDIO_RENDER_PARALLELISM":"10"}):
            self.assertEqual(render_parallelism(),2)

    def test_wav_duration_without_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav=Path(tmp)/"voice.wav"
            with wave.open(str(wav),"wb") as output:
                output.setparams((1,2,24000,0,"NONE","not compressed"))
                output.writeframes(b"\0\0"*36000)
            with patch("worker.ffprobe_duration") as probe:
                self.assertEqual(worker.narration_duration(wav),1.5)
                probe.assert_not_called()

    def test_shared_http_client_stays_open_until_session_exit(self):
        import asyncio
        async def examine(client):
            async with worker.stock_http_client(client) as shared:
                self.assertIs(shared,client)
            self.assertFalse(client.is_closed)
        with worker.stock_session() as (runner,client):
            runner.run(examine(client))
            runner.run(examine(client))
        self.assertTrue(client.is_closed)

    def test_pipeline_benchmark(self):
        def encode(i):
            time.sleep(.15)
            return i
        start=time.monotonic()
        for i in range(8):
            time.sleep(.15)
            encode(i)
        serial=time.monotonic()-start
        start=time.monotonic()
        output=[]
        with scene_pipeline(2) as pipeline:
            for i in range(8):
                if pipeline.full():
                    output.append(pipeline.pop()[0])
                time.sleep(.15)
                pipeline.submit(encode,None,i)
            output.extend(result for result,_ in pipeline.drain())
        elapsed=time.monotonic()-start
        self.assertEqual(output,list(range(8)))
        print(f"[benchmark] synthetic I/O+encode overlap: serial={serial:.3f}s pipeline={elapsed:.3f}s")
        self.assertLess(elapsed,serial*.9)

    def test_ffmpeg_same_frames_audio_and_timeline(self):
        def ffmpeg(*args):
            subprocess.run(["ffmpeg","-v","error","-y",*map(str,args)],check=True,timeout=45)
        def digest(path,stream):
            data=subprocess.check_output(["ffmpeg","-v","error","-i",str(path),"-map",stream,"-f","framemd5","-"],timeout=45)
            return hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); serial=root/"serial"; parallel=root/"parallel"
            serial.mkdir();parallel.mkdir()
            raw=root/"source.mp4"; audio=root/"speech.wav"; srt=root/"speech.srt"
            ffmpeg("-f","lavfi","-i","testsrc2=s=320x180:r=30","-t",2,"-c:v","libx264","-threads",1,raw)
            ffmpeg("-f","lavfi","-i","sine=frequency=440:sample_rate=24000","-t",2,audio)
            srt.write_text("1\n00:00:00,000 --> 00:00:01,900\nScience documentary\n")
            def render(i,directory):
                return worker.render_scene({"scene_number":i,"format":"16:9"},raw,audio,srt,directory,
                    duration=2,size=(320,180),bitrate=2700,shot_frames=[60])
            reference=[render(i,serial) for i in (1,2)]
            with scene_pipeline(2) as pipeline:
                for i in (1,2):
                    pipeline.submit(render,i,i,parallel)
                outputs=[item[0] for item in pipeline.drain()]
            for a,b in zip(reference,outputs):
                self.assertEqual(worker.verify_render_geometry(a,(320,180),2),worker.verify_render_geometry(b,(320,180),2))
                self.assertEqual(digest(a,"0:v:0"),digest(b,"0:v:0"))
                self.assertEqual(digest(a,"0:a:0"),digest(b,"0:a:0"))
            print("[quality] sequential and pipelined FFmpeg outputs have identical decoded frame/audio checksums")

if __name__=="__main__":
    unittest.main()
