import { ToolsModel } from '../models/tools_model.js';

const { ref, onMounted } = Vue;

export function useToolsController() {
    const model = new ToolsModel();
    const activeTools = ref([]);
    const isLoading = ref(false);
    const isSaving = ref(false);
    const showCreator = ref(false);

    // Form fields
    const toolName = ref('');
    const toolCode = ref('');
    const requiresConfirmation = ref(false);
    const editingToolName = ref('');
    const formError = ref('');

    const loadTools = async () => {
        try {
            isLoading.value = true;
            activeTools.value = await model.getTools();
        } catch (e) {
            console.error("Errore caricamento strumenti:", e);
            if (window.showToast) {
                window.showToast("Errore caricamento strumenti: " + e.message, "error");
            }
        } finally {
            isLoading.value = false;
        }
    };

    const loadBoilerplate = () => {
        const cleanedName = toolName.value.replace(/[^a-zA-Z0-9_]/g, '_').toLowerCase() || 'custom_tool';
        toolCode.value = [
            'def ' + cleanedName + '(arg1: str, arg2: int = 5) -> str:',
            '    """',
            '    Descrizione sintetica di cosa fa questo strumento.',
            '',
            '    Args:',
            '        arg1: Descrizione del primo parametro (stringa).',
            '        arg2: Descrizione del secondo parametro (intero).',
            '    """',
            '    # Scrivi qui la logica Python del tuo tool',
            '    result = f"Eseguito ' + cleanedName + ' con arg1={arg1} e arg2={arg2}"',
            '    return result',
            ''
        ].join('\n');
    };

    const saveTool = async () => {
        formError.value = '';
        const name = toolName.value.trim();
        const code = toolCode.value.trim();

        if (!name) {
            formError.value = "Il nome dello strumento è richiesto.";
            return;
        }
        if (!/^[a-zA-Z0-9_]+$/.test(name)) {
            formError.value = "Il nome può contenere solo lettere, numeri e underscore.";
            return;
        }
        if (!code) {
            formError.value = "Il codice dello strumento non può essere vuoto.";
            return;
        }

        try {
            isSaving.value = true;
            await model.saveCustomTool(name, code, requiresConfirmation.value);
            
            if (window.showToast) {
                window.showToast(`Strumento "${name}" salvato ed attivato con successo!`, "success");
            }
            
            // Reset form
            toolName.value = '';
            toolCode.value = '';
            requiresConfirmation.value = false;
            editingToolName.value = '';
            showCreator.value = false;
            
            await loadTools();
        } catch (e) {
            formError.value = e.message;
        } finally {
            isSaving.value = false;
        }
    };

    const deleteTool = async (name) => {
        if (!confirm(`Sei sicuro di voler eliminare lo strumento personalizzato "${name}"?`)) {
            return;
        }
        try {
            await model.deleteCustomTool(name);
            if (window.showToast) {
                window.showToast(`Strumento "${name}" eliminato con successo.`, "success");
            }
            await loadTools();
        } catch (e) {
            if (window.showToast) {
                window.showToast("Errore durante l'eliminazione: " + e.message, "error");
            }
        }
    };

    const editTool = (tool) => {
        toolName.value = tool.name;
        // strip confirmation flags from code view if present at the bottom
        let code = tool.source_code || '';
        const flagRegex = new RegExp(`\\n\\n${tool.name}\\.requires_confirmation\\s*=\\s*(True|False)\\n*`, 'g');
        code = code.replace(flagRegex, '');
        
        toolCode.value = code;
        requiresConfirmation.value = tool.requires_confirmation;
        editingToolName.value = tool.name;
        showCreator.value = true;
        formError.value = '';
    };

    const closeCreator = () => {
        toolName.value = '';
        toolCode.value = '';
        requiresConfirmation.value = false;
        editingToolName.value = '';
        showCreator.value = false;
        formError.value = '';
    };

    onMounted(() => {
        loadTools();
    });

    return {
        activeTools,
        isLoading,
        isSaving,
        showCreator,
        toolName,
        toolCode,
        requiresConfirmation,
        editingToolName,
        formError,
        loadTools,
        loadBoilerplate,
        saveTool,
        deleteTool,
        editTool,
        closeCreator
    };
}
