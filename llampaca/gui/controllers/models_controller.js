import { GgufModel } from '../models/gguf_model.js';

const { ref } = Vue;

export function useModelsController() {
    const model = new GgufModel();
    const models = ref(model.getModels());
    const downloadUrl = ref('');

    const startDownload = () => {
        if (!downloadUrl.value.trim()) {
            alert("Inserisci un percorso o repository GGUF valido!");
            return;
        }
        alert(`Avvio download del modello da: ${downloadUrl.value}\nIl completamento avverrà in background.`);
        downloadUrl.value = '';
    };

    const setDefaultModel = (id) => {
        model.setDefaultModel(id);
        models.value = model.getModels();
    };

    const deleteModel = (id) => {
        if (confirm("Sei sicuro di voler eliminare definitivamente questo file GGUF?")) {
            model.deleteModel(id);
            models.value = model.getModels();
        }
    };

    return {
        models,
        downloadUrl,
        startDownload,
        setDefaultModel,
        deleteModel
    };
}
