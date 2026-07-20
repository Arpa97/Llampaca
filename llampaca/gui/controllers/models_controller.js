import { GgufModel } from '../models/gguf_model.js';

const { ref, onMounted } = Vue;

export function useModelsController() {
    const model = new GgufModel();
    const models = ref([]);
    const downloadUrl = ref('');

    const loadModels = async () => {
        try {
            models.value = await model.getModels();
        } catch (e) {
            console.error("Errore caricamento modelli:", e);
        }
    };

    const startDownload = () => {
        if (!downloadUrl.value.trim()) {
            alert("Inserisci un percorso o repository GGUF valido!");
            return;
        }
        alert(`Avvio download del modello da: ${downloadUrl.value}\nIl completamento avverrà in background.`);
        downloadUrl.value = '';
    };

    const isRestarting = ref(false);

    const setDefaultModel = async (name) => {
        try {
            isRestarting.value = true;
            await model.setDefaultModel(name);
            await loadModels();
            if (window.showToast) {
                window.showToast(`Modello predefinito impostato su ${name}!`, 'success');
            }
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore: ${e.message}`, 'error');
            } else {
                alert("Errore durante l'impostazione del modello: " + e.message);
            }
        } finally {
            isRestarting.value = false;
        }
    };

    const deleteModel = async (name) => {
        if (confirm(`Sei sicuro di voler eliminare definitivamente il file GGUF "${name}"?`)) {
            try {
                await model.deleteModel(name);
                alert("Modello eliminato con successo.");
                await loadModels();
            } catch (e) {
                alert("Errore durante l'eliminazione: " + e.message);
            }
        }
    };

    onMounted(loadModels);

    return {
        models,
        downloadUrl,
        startDownload,
        setDefaultModel,
        deleteModel,
        isRestarting
    };
}
