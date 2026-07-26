import { GgufModel } from '../models/gguf_model.js';

const { ref, computed, onMounted, onUnmounted } = Vue;

export function useModelsController() {
    const model = new GgufModel();
    const models = ref([]);
    const downloadUrl = ref('');
    let pollInterval = null;

    // Split the flat catalog coming from the backend into the two kinds so the
    // view can render them as separate sections. Anything not explicitly marked
    // "embedding" (including custom local files with no kind) is a chat model.
    const chatModels = computed(() =>
        models.value.filter(m => (m.kind || 'chat') !== 'embedding' && m.kind !== 'image')
    );
    const embeddingModels = computed(() =>
        models.value.filter(m => m.kind === 'embedding')
    );
    const imageModels = computed(() =>
        models.value.filter(m => m.kind === 'image')
    );
    const projectorModels = computed(() =>
        models.value.filter(m => m.kind === 'projector')
    );

    const startPolling = () => {
        if (pollInterval) return;
        pollInterval = setInterval(async () => {
            await loadModels();
        }, 1500);
    };

    const stopPolling = () => {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
    };

    const loadModels = async () => {
        try {
            const prevModels = models.value;
            const newModels = await model.getModels();
            
            // Notify if a downloading model is now fully installed
            for (const newM of newModels) {
                const prevM = prevModels.find(p => p.id === newM.id);
                if (prevM && prevM.downloading && newM.installed) {
                    if (window.showToast) {
                        window.showToast(`Modello ${newM.name} scaricato con successo!`, 'success');
                    }
                }
            }
            
            models.value = newModels;
            
            // Manage polling based on active downloads
            const hasActiveDownloads = newModels.some(m => m.downloading);
            if (hasActiveDownloads) {
                startPolling();
            } else {
                stopPolling();
            }
        } catch (e) {
            console.error("Errore caricamento modelli:", e);
        }
    };

    const startDownload = async () => {
        const val = downloadUrl.value.trim();
        if (!val) {
            if (window.showToast) {
                window.showToast("Inserisci un percorso o repository GGUF valido!", "error");
            } else {
                alert("Inserisci un percorso o repository GGUF valido!");
            }
            return;
        }
        try {
            const res = await model.downloadModel(null, null, val);
            if (window.showToast) {
                window.showToast(`Avvio download di ${res.filename}...`, "success");
            }
            downloadUrl.value = '';
            await loadModels();
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore: ${e.message}`, "error");
            } else {
                alert(`Errore: ${e.message}`);
            }
        }
    };

    const downloadPreset = async (repoId, filename) => {
        try {
            await model.downloadModel(repoId, filename, null);
            if (window.showToast) {
                window.showToast(`Avvio download di ${filename}...`, 'success');
            }
            await loadModels();
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore: ${e.message}`, 'error');
            } else {
                alert(`Errore: ${e.message}`);
            }
        }
    };

    onUnmounted(stopPolling);

    const isRestarting = ref(false);

    // kind: "chat" restarts the llama-server (chat model swap needs a reload,
    // hence the blocking overlay); "embedding" only persists the choice, so no
    // overlay is shown for it.
    const setDefaultModel = async (name, kind = 'chat') => {
        const isChat = kind !== 'embedding' && kind !== 'image';
        try {
            if (isChat) isRestarting.value = true;
            await model.setDefaultModel(name, kind);
            await loadModels();
            window.dispatchEvent(new CustomEvent('llampaca:status-changed'));
            if (window.showToast) {
                let label = 'Modello di chat predefinito';
                if (kind === 'embedding') label = 'Modello di embedding predefinito';
                if (kind === 'image') label = 'Modello immagini predefinito';
                window.showToast(`${label} impostato su ${name}!`, 'success');
            }
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore: ${e.message}`, 'error');
            } else {
                alert("Errore durante l'impostazione del modello: " + e.message);
            }
        } finally {
            if (isChat) isRestarting.value = false;
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

    const searchQuery = ref('');
    const searchResults = ref([]);
    const isSearching = ref(false);
    const currentPage = ref(1);
    const hasNextPage = ref(false);
    const currentQuery = ref('');

    const performSearch = async (query = '', isLoadMore = false) => {
        try {
            isSearching.value = true;
            if (!isLoadMore) {
                currentPage.value = 1;
                currentQuery.value = query;
                searchResults.value = [];
                hasNextPage.value = false;
            }
            const data = await model.searchHfModels(currentQuery.value, currentPage.value);
            const mapped = data.models.map(r => {
                return {
                    ...r,
                    selected_file: r.files && r.files.length ? r.files[0].filename : ''
                };
            });
            
            if (isLoadMore) {
                searchResults.value = [...searchResults.value, ...mapped];
            } else {
                searchResults.value = mapped;
            }
            
            if (data.pagination) {
                hasNextPage.value = data.pagination.hasNextPage || false;
            }
        } catch (e) {
            console.error("Errore ricerca Hugging Face:", e);
            if (window.showToast) {
                window.showToast(`Errore ricerca: ${e.message}`, 'error');
            }
        } finally {
            isSearching.value = false;
        }
    };

    const loadMore = async () => {
        if (hasNextPage.value) {
            currentPage.value++;
            await performSearch(currentQuery.value, true);
        }
    };

    const downloadSearchModel = async (repoId, filename, mmprojFilename = null) => {
        if (!filename) {
            if (window.showToast) {
                window.showToast("Seleziona prima un file GGUF!", "error");
            }
            return;
        }
        try {
            // Se c'è un mmproj, lo passiamo al backend nel body.
            // Il modello (in lib/model.js) dovrà supportare un nuovo parametro o accodarlo all'oggetto payload.
            await model.downloadModel(repoId, filename, null, mmprojFilename);
            if (window.showToast) {
                window.showToast(`Avvio download di ${filename}...`, 'success');
                if (mmprojFilename) {
                    window.showToast(`Avvio download projector ${mmprojFilename}...`, 'success');
                }
            }
            await loadModels();
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore: ${e.message}`, 'error');
            } else {
                alert(`Errore: ${e.message}`);
            }
        }
    };

    onMounted(() => {
        loadModels();
        performSearch('');
    });

    return {
        models,
        chatModels,
        embeddingModels,
        imageModels,
        projectorModels,
        downloadUrl,
        startDownload,
        setDefaultModel,
        deleteModel,
        isRestarting,
        downloadPreset,
        searchQuery,
        searchResults,
        isSearching,
        performSearch,
        downloadSearchModel,
        currentPage,
        hasNextPage,
        loadMore
    };
}
