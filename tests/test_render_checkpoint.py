"""Verify save-stage recovery without live credentials or publishing."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import worker
from studio_master import save_upload_master, download_upload_master

def fixture():
    row = {"id":"example", "status":"rendering", "title":"Example",
           "scenes":[{"narration":"A complete scene.", "search_queries":["laboratory"],
                      "_studio":{"duration_seconds":60,"format":"16:9"}}]}
    scenes, ratio, details = worker.video_details(row)
    cp = {"version":1, "fingerprint":worker.checkpoint_fingerprint(scenes,ratio,details),
          "master":{"sha256":"saved"}, "timeline":[{"duration":60,"start":0}],
          "seconds":60.02,"expected_seconds":60,"size":[1920,1080],"ratio":"16:9",
          "headline":"Example","chapters":[],"voice":"voice","engine":"kokoro","rate":"+0%","pitch":"+0Hz"}
    row["scenes"][0]["_studio"]["render_checkpoint"]=cp
    return row, cp

class CheckpointTest(unittest.TestCase):
    def test_process_restores_without_generating_narration_or_scenes(self):
        row, cp=fixture()
        with tempfile.TemporaryDirectory() as tmp, patch("worker.ROOT",Path(tmp)), \
             patch("worker.prepare_revision") as render, patch("worker.resume_checkpoint") as resume:
            worker.process(row,Mock())
            resume.assert_called_once()
            self.assertEqual(resume.call_args.args[4],cp)
            render.assert_not_called()

    def test_recovery_checks_download_and_only_finishes(self):
        row, cp=fixture()
        client=Mock()
        client.storage.from_.return_value.download.return_value=b"thumbnail"
        with tempfile.TemporaryDirectory() as tmp, patch("worker.save_fields"), \
             patch("worker.download_upload_master") as download, \
             patch("worker.verify_render_geometry",return_value=("16:9",60.02)) as geometry, \
             patch("worker.validate_audio") as audio, patch("worker.verify_thumbnail"), \
             patch("worker.finish_checkpoint") as finish:
            worker.resume_checkpoint(client,row,Path(tmp),cp["fingerprint"],cp)
            download.assert_called_once()
            geometry.assert_called_once_with(Path(tmp)/"checkpoint_master.mp4",(1920,1080),60)
            audio.assert_called_once()
            finish.assert_called_once()

    def test_wrong_fingerprint_refuses_to_use_stale_render(self):
        row,cp=fixture()
        with patch("worker.download_upload_master") as download:
            with self.assertRaisesRegex(ValueError,"does not match"):
                worker.resume_checkpoint(Mock(),row,Path("."),"different",cp)
            download.assert_not_called()

    def test_failed_preview_keeps_checkpoint_then_success_clears_it(self):
        row,cp=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            final=Path(tmp)/"master.mp4"
            thumb=Path(tmp)/"thumbnail.jpg"
            thumb.write_bytes(b"thumbnail")
            with patch("worker.save_fields") as save, patch("worker.verify_thumbnail"), \
                 patch("worker.upload_thumbnail",return_value="thumbnail"), \
                 patch("worker.upload_mp4",side_effect=[RuntimeError("failed save"),("preview",cp["master"],360)]) as upload:
                with self.assertRaisesRegex(RuntimeError,"failed save"):
                    worker.finish_checkpoint(Mock(),row,final,cp,thumb)
                self.assertFalse(any(call.kwargs.get("status")=="completed" for call in save.call_args_list))
                self.assertEqual(row["scenes"][0]["_studio"]["render_checkpoint"],cp)
                worker.finish_checkpoint(Mock(),row,final,cp,thumb)
                completed=save.call_args.kwargs
                self.assertEqual(completed["status"],"completed")
                self.assertIsNone(completed["render_checkpoint"])
                self.assertEqual(completed["upload_master"],cp["master"])
                self.assertEqual(completed["publish_status"],"awaiting_review")
                self.assertFalse(completed["publish_requested"])
                self.assertIsNone(completed["publish_approved_at"])
                self.assertEqual(upload.call_args.kwargs["checkpoint_master"],cp["master"])

    def test_preview_upload_failure_does_not_delete_saved_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            final=Path(tmp)/"master.mp4"; final.write_bytes(b"master")
            client=Mock()
            client.storage.from_.return_value.upload.side_effect=RuntimeError("storage failure")
            with patch("worker.storage_preview",return_value=final),patch("worker.validate_audio"), \
                 patch("worker.ffprobe_duration",return_value=60),patch("worker.check_media_budget"), \
                 patch("worker.remove_upload_master") as remove,patch("worker.save_upload_master") as resave:
                with self.assertRaisesRegex(RuntimeError,"storage failure"):
                    worker.upload_mp4(client,final,"example",checkpoint_master={"sha256":"saved"})
                remove.assert_not_called()
                resave.assert_not_called()

    def test_force_master_even_if_source_is_small_then_checksum_restore(self):
        with tempfile.TemporaryDirectory() as tmp,patch("studio_master.PART_BYTES",8):
            path=Path(tmp)/"final.mp4"; path.write_bytes(b"hello world")
            client=Mock(); objects={}
            bucket=client.storage.from_.return_value
            bucket.list.return_value=[]
            bucket.upload.side_effect=lambda name,data,**kwargs:objects.update({name:data})
            bucket.download.side_effect=objects.__getitem__
            with patch("studio_master.bucket_exists",return_value=True):
                manifest=save_upload_master(client,"example",path,path,"renders","example.mp4",force_master=True,preview_reserve=11)
            restored=Path(tmp)/"restored.mp4"
            download_upload_master(client,"example",manifest,restored)
            self.assertEqual(path.read_bytes(),restored.read_bytes())
            self.assertEqual(manifest["parts"],2)

if __name__=="__main__":
    unittest.main()
