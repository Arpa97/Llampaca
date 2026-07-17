import { McpModel } from '../models/mcp_model.js';

const { ref, computed } = Vue;

export function useMcpController() {
    const model = new McpModel();
    const activeIntegrations = ref(model.getActiveIntegrations());
    const registryItems = ref(model.getRegistryItems());
    const registrySearch = ref('');

    const getFilteredRegistry = computed(() => {
        const search = registrySearch.value.toLowerCase().trim();
        if (!search) return registryItems.value;
        return registryItems.value.filter(item => 
            item.name.toLowerCase().includes(search) || 
            item.description.toLowerCase().includes(search)
        );
    });

    const removeIntegration = (name) => {
        if (confirm(`Sei sicuro di voler disinstallare l'integrazione '${name}'?`)) {
            model.removeIntegration(name);
            activeIntegrations.value = model.getActiveIntegrations();
        }
    };

    const installRegistryItem = (item) => {
        const exists = activeIntegrations.value.some(i => i.name === item.name);
        if (exists) {
            alert(`L'integrazione '${item.name}' è già installata ed attiva!`);
            return;
        }
        alert(`Avvio procedura guidata per '${item.name}'...\n(Apri il terminale per completare la configurazione e le credenziali)`);
        
        model.installIntegration(item);
        activeIntegrations.value = model.getActiveIntegrations();
    };

    return {
        activeIntegrations,
        registrySearch,
        getFilteredRegistry,
        removeIntegration,
        installRegistryItem
    };
}
