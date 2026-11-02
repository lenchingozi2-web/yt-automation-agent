"use client";

import { useEffect, useState } from "react";
import { supabase } from "../src/lib/supabase";
import { Play, Edit3, Loader2, X, Sparkles, FileText } from "lucide-react";

export default function Dashboard() {
  const [videos, setVideos] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [selectedVideo, setSelectedVideo] = useState<any>(null);
  
  // Inputs state
  const [inputMode, setInputMode] = useState<'ai' | 'manual'>('ai');
  const [prompt, setPrompt] = useState("");
  const [manualScript, setManualScript] = useState("");

  useEffect(() => {
    fetchVideos();
  }, []);

  async function fetchVideos() {
    const { data, error } = await supabase
      .from("videos")
      .select("*")
      .order("created_at", { ascending: false });

    if (error) console.error("Error fetching videos:", error);
    else setVideos(data || []);
    setLoading(false);
  }

  async function handleCreate() {
    if (!prompt && !manualScript) return;
    setGenerating(true);
    
    if (inputMode === 'ai') {
      try {
        const res = await fetch('/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt })
        });
        
        const generatedData = await res.json();
        
        const newTask = {
          title: generatedData.title || "AI Generated Script",
          description: generatedData.description || "",
          bgm_query: generatedData.bgm_query || "cinematic",
          scenes: generatedData.scenes || [],
          status: "pending"
        };

        await supabase.from("videos").insert([newTask]);
        await fetchVideos();
        setPrompt("");
      } catch (error) {
        console.error("Failed to generate script:", error);
        alert("Generation failed. Check the console for details.");
      }
    } else {
      const manualTask = {
        title: "Manual Script Upload",
        description: "Custom user script",
        status: "pending",
        scenes: [{ scene_number: 1, narration: manualScript, search_queries: ["custom video"] }]
      };
      await supabase.from("videos").insert([manualTask]);
      await fetchVideos();
      setManualScript("");
    }
    
    setGenerating(false);
  }

  async function handleRender(id: string) {
    const { error } = await supabase.from("videos").update({ status: "rendering" }).eq("id", id);
    if (!error) await fetchVideos();
  }

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-8 relative">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900">LenxiQ Studio</h1>
        <p className="text-gray-500 mt-1">Design, edit, and deploy YouTube automation pipelines.</p>
      </header>

      {/* Creation Panel */}
      <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-200 mb-10">
        <div className="flex gap-4 mb-4 border-b border-gray-100 pb-4">
          <button onClick={() => setInputMode('ai')} className={`flex items-center gap-2 font-medium px-4 py-2 rounded-lg transition ${inputMode === 'ai' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>
            <Sparkles size={18} /> AI Generator
          </button>
          <button onClick={() => setInputMode('manual')} className={`flex items-center gap-2 font-medium px-4 py-2 rounded-lg transition ${inputMode === 'manual' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>
            <FileText size={18} /> Manual Script
          </button>
        </div>

        {inputMode === 'ai' ? (
          <div>
            <textarea 
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g., Generate a 3-minute video about the history of artificial intelligence..."
              className="w-full p-4 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              rows={3}
            />
          </div>
        ) : (
          <div>
            <textarea 
              value={manualScript}
              onChange={(e) => setManualScript(e.target.value)}
              placeholder="Paste your complete script here. Separate scenes with line breaks..."
              className="w-full p-4 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none font-mono text-sm"
              rows={5}
            />
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <button 
            onClick={handleCreate}
            disabled={generating || (!prompt && !manualScript)}
            className="bg-black text-white px-6 py-2.5 rounded-lg font-medium flex items-center gap-2 hover:bg-gray-800 transition disabled:opacity-50"
          >
            {generating ? <Loader2 className="animate-spin" size={18} /> : <Sparkles size={18} />}
            {generating ? "Processing..." : inputMode === 'ai' ? "Generate AI Script" : "Import Script"}
          </button>
        </div>
      </div>

      {/* Video Grid */}
      {loading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="animate-spin text-gray-400" size={32} />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {videos.map((video) => (
            <div key={video.id} className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
              <div className="p-6 flex-grow">
                <div className="flex justify-between items-start mb-4">
                  <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${
                    video.status === 'pending_generation' ? 'bg-purple-100 text-purple-800' :
                    video.status === 'rendering' ? 'bg-amber-100 text-amber-800' : 'bg-green-100 text-green-800'
                  }`}>
                    {video.status.replace('_', ' ').toUpperCase()}
                  </span>
                </div>
                <h3 className="text-xl font-bold mb-2 line-clamp-2">{video.title}</h3>
                <p className="text-gray-600 text-sm line-clamp-3 mb-4">{video.description}</p>
              </div>
              <div className="border-t border-gray-100 p-4 bg-gray-50 flex justify-end gap-3">
                <button 
                  onClick={() => setSelectedVideo(video)}
                  disabled={video.scenes?.length === 0}
                  className="text-gray-600 hover:text-black font-medium text-sm px-3 py-1.5 flex items-center gap-2 transition disabled:opacity-30"
                >
                  <Edit3 size={16} /> Review Script
                </button>
                <button 
                  onClick={() => handleRender(video.id)}
                  disabled={video.status === 'rendering' || video.scenes?.length === 0}
                  className={`font-medium text-sm px-4 py-1.5 rounded-lg flex items-center gap-2 transition shadow-sm ${
                    video.status === 'rendering' ? 'bg-amber-500 text-white cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700 text-white'
                  }`}
                >
                  {video.status === 'rendering' ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                  {video.status === 'rendering' ? 'Rendering...' : 'Render'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Script Review Modal */}
      {selectedVideo && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden">
            <div className="p-6 border-b border-gray-100 flex justify-between items-start">
              <div>
                <h2 className="text-2xl font-bold">{selectedVideo.title}</h2>
                <p className="text-gray-500 text-sm mt-1">{selectedVideo.description}</p>
              </div>
              <button onClick={() => setSelectedVideo(null)} className="text-gray-400 hover:text-black transition">
                <X size={24} />
              </button>
            </div>
            <div className="p-6 overflow-y-auto bg-gray-50 flex-grow">
              <div className="space-y-4">
                {selectedVideo.scenes?.map((scene: any, idx: number) => (
                  <div key={idx} className="bg-white p-4 rounded-xl border border-gray-200 shadow-sm">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">Scene {scene.scene_number}</span>
                      <span className="text-xs text-blue-600 bg-blue-50 px-2 py-1 rounded font-mono">{scene.search_queries?.join(", ")}</span>
                    </div>
                    <textarea 
                      defaultValue={scene.narration} 
                      className="w-full text-gray-800 bg-transparent resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 rounded p-1"
                      rows={3}
                    />
                  </div>
                ))}
              </div>
            </div>
            <div className="p-6 border-t border-gray-100 bg-white flex justify-end gap-3">
              <button onClick={() => setSelectedVideo(null)} className="px-5 py-2.5 text-gray-600 font-medium hover:bg-gray-100 rounded-lg transition">Cancel</button>
              <button className="px-5 py-2.5 bg-black text-white font-medium rounded-lg hover:bg-gray-800 transition">Save Changes</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}