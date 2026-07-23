import { McpModel } from '../models/mcp_model.js';

const { ref, onMounted } = Vue;

export function useMcpController() {
    const model = new McpModel();
    const activeIntegrations = ref([]);
    const registrySearch = ref('');
    const searchResults = ref([]);
    const isSearching = ref(false);
    const isInstalling = ref(false);
    const currentPage = ref(1);
    const totalPages = ref(1);
    const currentQuery = ref('');
    // Installation configuration states
    const installingItem = ref(null);
    const envInputs = ref({});
    const customCommand = ref('npx');
    const customArgs = ref('');

    const loadActiveIntegrations = async () => {
        try {
            activeIntegrations.value = await model.getActiveIntegrations();
        } catch (e) {
            console.error("Errore caricamento integrazioni attive:", e);
        }
    };

    const performSearch = async (query = '', isLoadMore = false) => {
        try {
            isSearching.value = true;

            if (!isLoadMore) {
                currentPage.value = 1;
                currentQuery.value = query;
                totalPages.value = 1;
                searchResults.value = [];
            }
            const data = await model.searchRegistry(currentQuery.value, currentPage.value);
            // Se il registro risponde 200 ma senza la lista attesa, senza questo
            // fallback searchResults diventa undefined: il template legge
            // .length su undefined e l'intera scheda resta bianca.
            const servers = Array.isArray(data.servers) ? data.servers : [];
            if (isLoadMore) {
                searchResults.value = [...searchResults.value, ...servers];
            } else {
                searchResults.value = servers;
            }

            if (data.pagination) {
                totalPages.value = data.pagination.totalPages || 1;
            }
        } catch (e) {
            console.error("Errore ricerca registro MCP:", e);
            if (window.showToast) {
                window.showToast(`Errore ricerca: ${e.message}`, 'error');
            }
        } finally {
            isSearching.value = false;
        }
    };

    const loadMore = async () => {
        if (currentPage.value < totalPages.value) {
            currentPage.value++;
            await performSearch(currentQuery.value, true);
        }
    };

    const removeIntegration = async (name) => {
        if (confirm(`Sei sicuro di voler disinstallare l'integrazione '${name}'?`)) {
            try {
                isInstalling.value = true;
                await model.uninstallIntegration(name);
                if (window.showToast) {
                    window.showToast(`Integrazione '${name}' disinstallata con successo.`, 'success');
                }
                await loadActiveIntegrations();
            } catch (e) {
                if (window.showToast) {
                    window.showToast(`Errore disinstallazione: ${e.message}`, 'error');
                } else {
                    alert(`Errore: ${e.message}`);
                }
            } finally {
                isInstalling.value = false;
            }
        }
    };

    const triggerInstall = async (item) => {
        try {
            isInstalling.value = true;
            const schema = await model.getConfigSchema(item.slug);
            item.environmentVariablesJsonSchema = schema;

            installingItem.value = item;
            customCommand.value = "npx";
            customArgs.value = `-y @smithery/cli run ${item.slug}`;

            envInputs.value = {};
            const props = schema.properties || {};
            for (const k in props) {
                envInputs.value[k] = '';
            }
        } catch (e) {
            console.error("Errore recupero schema configurazione:", e);
            if (window.showToast) {
                window.showToast(`Errore caricamento parametri: ${e.message}`, 'error');
            } else {
                alert(`Errore: ${e.message}`);
            }
        } finally {
            isInstalling.value = false;
        }
    };

    const cancelInstall = () => {
        installingItem.value = null;
        envInputs.value = {};
    };

    const confirmInstall = async () => {
        if (!installingItem.value) return;

        const item = installingItem.value;
        const name = item.slug || item.name.toLowerCase().replace(/\s+/g, '-');

        const schema = item.environmentVariablesJsonSchema || {};
        const required = schema.required || [];
        for (const k of required) {
            if (!envInputs.value[k] || !envInputs.value[k].trim()) {
                if (window.showToast) {
                    window.showToast(`La variabile d'ambiente '${k}' è obbligatoria!`, 'error');
                } else {
                    alert(`La variabile '${k}' è obbligatoria.`);
                }
                return;
            }
        }

        try {
            isInstalling.value = true;
            const args = customArgs.value.trim().split(/\s+/).filter(Boolean);

            await model.installIntegration(
                name,
                customCommand.value.trim(),
                args,
                envInputs.value
            );

            if (window.showToast) {
                window.showToast(`Integrazione '${name}' installata con successo!`, 'success');
            }

            installingItem.value = null;
            envInputs.value = {};
            await loadActiveIntegrations();
        } catch (e) {
            if (window.showToast) {
                window.showToast(`Errore installazione: ${e.message}`, 'error');
            } else {
                alert(`Errore: ${e.message}`);
            }
        } finally {
            isInstalling.value = false;
        }
    };

    onMounted(() => {
        loadActiveIntegrations();
        performSearch('');
    });

    return {
        activeIntegrations,
        registrySearch,
        searchResults,
        isSearching,
        isInstalling,
        installingItem,
        envInputs,
        customCommand,
        customArgs,
        performSearch,
        removeIntegration,
        triggerInstall,
        cancelInstall,
        confirmInstall,
        loadActiveIntegrations,
        loadMore,
        currentPage,
        totalPages
    };
}
