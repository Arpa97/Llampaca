import { SettingsModel } from '../models/settings_model.js';

const { ref, onMounted } = Vue;

export function useSettingsController() {
    const model = new SettingsModel();
    const settings = ref({
        port: 8080,
        contextSize: 32768,
        threads: 4,
        gpuLayers: -1,
        // Embedder GPU offload: 0 = CPU-only (default), -1 = auto, N = N layers.
        embeddingGpuLayers: 0,
        // Salta la fase di ragionamento dei modelli come Qwen3.
        noThink: false
    });

    const loadSettings = async () => {
        try {
            settings.value = await model.getSettings();
        } catch (e) {
            console.error("Errore nel caricamento delle impostazioni:", e);
        }
    };

    const isSaving = ref(false);

    const waitForServer = async () => {
        // Wait 1.5s to give the background task time to stop the old server
        await new Promise(r => setTimeout(r, 1500));
        let attempts = 0;
        while (attempts < 60) {
            try {
                const res = await fetch(`/api/server/status?_t=${Date.now()}`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.running) return;
                }
            } catch (e) {}
            await new Promise(r => setTimeout(r, 1000));
            attempts++;
        }
    };

    const saveSettings = async () => {
        try {
            isSaving.value = true;
            settings.value = await model.saveSettings(settings.value);
            await waitForServer();
            // Riallinea il blocco di stato nella sidebar (porta e contesto appena salvati)
            window.dispatchEvent(new CustomEvent('llampaca:status-changed'));
            if (window.showToast) {
                window.showToast("Impostazioni salvate", "success");
            }
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore: ${e.message}`, "error");
            } else {
                alert("Errore durante il salvataggio: " + e.message);
            }
        } finally {
            isSaving.value = false;
        }
    };

    onMounted(loadSettings);

    return {
        settings,
        saveSettings,
        isSaving
    };
}
