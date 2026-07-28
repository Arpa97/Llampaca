export class SettingsModel {
    async getSettings() {
        const r = await fetch(`/api/settings?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        return {
            port: data.server_port,
            contextSize: data.context_size,
            threads: data.n_threads,
            gpuLayers: data.gpu_layers,
            // Embedder's own GPU offload, independent from the chat model's.
            embeddingGpuLayers: data.embedding_gpu_layers,
            // Skip the reasoning phase of models like Qwen3. Applied per
            // request, so it takes effect on the next message with no restart.
            noThink: !!data.no_think,
            kvCacheType: data.kv_cache_quant_k || "q8_0",
            draftModel: data.draft_model || "",
            draftGpuLayers: data.draft_model_gpu_layers !== undefined ? data.draft_model_gpu_layers : -1
        };
    }

    async saveSettings(newSettings) {
        const payload = {
            server_port: parseInt(newSettings.port),
            context_size: parseInt(newSettings.contextSize),
            n_threads: parseInt(newSettings.threads),
            gpu_layers: parseInt(newSettings.gpuLayers),
            embedding_gpu_layers: parseInt(newSettings.embeddingGpuLayers),
            kv_cache_type: newSettings.kvCacheType,
            draft_model: newSettings.draftModel,
            draft_model_gpu_layers: parseInt(newSettings.draftGpuLayers)
            // no_think NON viaggia da qui: lo possiede il pulsante ⚡ in chat.
            // Inviarlo da questo form lo riporterebbe al valore letto
            // all'apertura della scheda, annullando in silenzio una scelta
            // fatta nel frattempo dal composer.
        };
        const r = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        return {
            port: data.config.server_port,
            contextSize: data.config.context_size,
            threads: data.config.n_threads,
            gpuLayers: data.config.gpu_layers,
            embeddingGpuLayers: data.config.embedding_gpu_layers,
            noThink: !!data.config.no_think,
            kvCacheType: data.config.kv_cache_quant_k || "q8_0",
            draftModel: data.config.draft_model || "",
            draftGpuLayers: data.config.draft_model_gpu_layers !== undefined ? data.config.draft_model_gpu_layers : -1
        };
    }
}
