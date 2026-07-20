import { SettingsModel } from '../models/settings_model.js';

const { ref, onMounted } = Vue;

export function useSettingsController() {
    const model = new SettingsModel();
    const settings = ref({
        port: 8080,
        contextSize: 32768,
        threads: 4,
        gpuLayers: -1
    });

    const loadSettings = async () => {
        try {
            settings.value = await model.getSettings();
        } catch (e) {
            console.error("Errore nel caricamento delle impostazioni:", e);
        }
    };

    const isSaving = ref(false);

    const saveSettings = async () => {
        try {
            isSaving.value = true;
            settings.value = await model.saveSettings(settings.value);
            if (window.showToast) {
                window.showToast("Impostazioni salvate con successo!", "success");
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
